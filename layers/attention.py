import torch
from torch import nn
from torch.nn import functional as F


class BahdanauAttention(nn.Module):
    def __init__(self, annot_dim, query_dim, hidden_dim):
        super(BahdanauAttention, self).__init__()
        self.query_layer = nn.Linear(query_dim, hidden_dim, bias=True)
        self.annot_layer = nn.Linear(annot_dim, hidden_dim, bias=True)
        self.v = nn.Linear(hidden_dim, 1, bias=False)

    def forward(self, annots, query):
        """
        Shapes:
            - annots: (batch, max_time, dim)
            - query: (batch, 1, dim) or (batch, dim)
        """
        if query.dim() == 2:
            # insert time-axis for broadcasting
            query = query.unsqueeze(1)
        # (batch, 1, dim)
        processed_query = self.query_layer(query)
        processed_annots = self.annot_layer(annots)

        # (batch, max_time, 1)
        alignment = self.v(nn.functional.tanh(
            processed_query + processed_annots))

        # (batch, max_time)
        return alignment.squeeze(-1)
    
    
class LocationSensitiveAttention(nn.Module):
    """Location sensitive attention following
    https://arxiv.org/pdf/1506.07503.pdf"""
    def __init__(self, annot_dim, query_dim, hidden_dim):
        super(LocationSensitiveAttention, self).__init__()
        loc_kernel_size = 31
        loc_dim = 32
        padding = int((loc_kernel_size -1) / 2)
        self.loc_conv =  nn.Conv1d(2, loc_dim,
                                   kernel_size=loc_kernel_size, stride=1,
                                   padding=padding, bias=False)
        self.loc_linear = nn.Linear(loc_dim, hidden_dim)
        self.query_layer = nn.Linear(query_dim, hidden_dim, bias=True)
        self.annot_layer = nn.Linear(annot_dim, hidden_dim, bias=True)
        self.v = nn.Linear(hidden_dim, 1, bias=False)
        
    def forward(self, annot, query, loc):
        """
        Shapes:
            - annots: (batch, max_time, dim)
            - query: (batch, 1, dim) or (batch, dim)
            - loc: (batch, 2, max_time)
        """
        if query.dim() == 2:
            # insert time-axis for broadcasting
            query = query.unsqueeze(1)
        loc_conv = self.loc_conv(loc)
        loc_conv = loc_conv.transpose(1, 2)
        processed_loc = self.loc_linear(loc_conv)
        processed_query = self.query_layer(query)
        processed_annots = self.annot_layer(annot)
        alignment = self.v(nn.functional.tanh(
            processed_query + processed_annots + processed_loc))
        # (batch, max_time)
        return alignment.squeeze(-1)


class AttentionRNN(nn.Module):
    def __init__(self, out_dim, annot_dim, memory_dim, align_model):
        r"""
        General Attention RNN wrapper
        
        Args:
            out_dim (int): context vector feature dimension.
            annot_dim (int): annotation vector feature dimension.
            memory_dim (int): memory vector (decoder autogression) feature dimension.
            align_model (str): 'b' for Bahdanau, 'ls' Location Sensitive alignment.
        """
        super(AttentionRNN, self).__init__()
        self.rnn_cell = nn.GRUCell(out_dim + memory_dim, out_dim)
        # pick bahdanau or location sensitive attention
        if align_model == 'b':
            self.alignment_model = BahdanauAttention(annot_dim, out_dim, out_dim)
        if align_model == 'ls':
            self.alignment_model = LocationSensitiveAttention(annot_dim, out_dim, out_dim)
        else:
            raise RuntimeError(" Wrong alignment model name: {}. Use\
                'b' (Bahdanau) or 'ls' (Location Sensitive).".format(align_model))
            

    def forward(self, memory, context, rnn_state, annotations,
                attention_vec, mask=None, annotations_lengths=None):

        # Concat input query and previous context context
        rnn_input = torch.cat((memory, context), -1)
        #rnn_input = rnn_input.unsqueeze(1)

        # Feed it to RNN
        # s_i = f(y_{i-1}, c_{i}, s_{i-1})
        rnn_output = self.rnn_cell(rnn_input, rnn_state)

        # Alignment
        # (batch, max_time)
        # e_{ij} = a(s_{i-1}, h_j)
        if attnetion_vec is None:
            alignment = self.alignment_model(annotations, rnn_output)
        else:
            alignment = self.alignment_model(annotations, rnn_output, attention_vec)

        # TODO: needs recheck.
        if mask is not None:
            mask = mask.view(query.size(0), -1)
            alignment.data.masked_fill_(mask, self.score_mask_value)

        # Normalize context weight
        alignment = F.softmax(alignment, dim=-1)

        # Attention context vector
        # (batch, 1, dim)
        # c_i = \sum_{j=1}^{T_x} \alpha_{ij} h_j
        context = torch.bmm(alignment.unsqueeze(1), annotations)
        context = context.squeeze(1)
        return rnn_output, context, alignment

