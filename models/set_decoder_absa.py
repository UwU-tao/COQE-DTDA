import torch.nn as nn
import torch
from transformers.models.bert.modeling_bert import BertIntermediate, BertOutput, BertAttention, BertSelfAttention
# transformers==4.10.0
from pdb import set_trace as stop

class SetDecoder_absa(nn.Module):
    def __init__(self, args, config, num_generated_triples, num_layers, num_classes, return_intermediate=False):
        super().__init__()

        self.args = args
        config.hidden_size = config.hidden_size
        # if args.use_last_hidden_state == "True":
        #     config.hidden_size = config.hidden_size
        # elif args.use_last_hidden_state == "False":
        #     config.hidden_size = 4 * config.hidden_size

        self.return_intermediate = return_intermediate
        self.num_generated_triples = num_generated_triples
        self.layers = nn.ModuleList([DecoderLayer(config) for _ in range(num_layers)])
        self.LayerNorm = nn.LayerNorm(config.hidden_size, eps=config.layer_norm_eps)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.query_embed = nn.Embedding(num_generated_triples, config.hidden_size)
        self.decoder2class = nn.Linear(config.hidden_size, num_classes)

        if self.args.multi_heads == 5:
            self.decoder2span = nn.Linear(config.hidden_size, 4) # changed 4-->2
            self.metric_1 = nn.Linear(config.hidden_size, config.hidden_size * 8) # changed 8-->4
            self.metric_2 = nn.Linear(config.hidden_size, config.hidden_size * 8) # changed 8-->4
            self.metric_3 = nn.Linear(config.hidden_size * 8, 1 * 8, bias=False) # changed 8-->4
        else:
            self.decoder2span = nn.Linear(config.hidden_size, 2) # changed 4-->2
            self.metric_1 = nn.Linear(config.hidden_size, config.hidden_size * 4) # changed 8-->4
            self.metric_2 = nn.Linear(config.hidden_size, config.hidden_size * 4) # changed 8-->4
            self.metric_3 = nn.Linear(config.hidden_size * 4, 1 * 4, bias=False) # changed 8-->4

        torch.nn.init.orthogonal_(self.metric_1.weight, gain=1)
        torch.nn.init.orthogonal_(self.metric_2.weight, gain=1)
        torch.nn.init.orthogonal_(self.metric_3.weight, gain=1)
        torch.nn.init.orthogonal_(self.query_embed.weight, gain=1)


    def forward(self, encoder_hidden_states, encoder_attention_mask):
        """
        encoder_hidden_states: [bsz, enc_len, hidden]
        encoder_attention_mask: [bsz, enc_len, enc_len]
        """
        bsz = encoder_hidden_states.size()[0]
        hidden_states = self.query_embed.weight.unsqueeze(0).repeat(bsz, 1, 1)
        # bsz, q_num, hidden
        hidden_states = self.dropout(self.LayerNorm(hidden_states))
        all_hidden_states = ()

        for i, layer_module in enumerate(self.layers):
            if self.return_intermediate:
                all_hidden_states = all_hidden_states + (hidden_states,)
            layer_outputs = layer_module(
                hidden_states, encoder_hidden_states, encoder_attention_mask
            )
            hidden_states = layer_outputs[0] # bsz, q_num, hidden_size
        
        class_logits = self.decoder2class(hidden_states) # bsz, q_num, hidden--> bsz, q_num, clss_num

        logits = self.metric_3(torch.tanh(
            self.metric_1(hidden_states).unsqueeze(2) + self.metric_2(
                encoder_hidden_states
            ).unsqueeze(1)
        )) # logits: bsz, q_num, seq_len, 8
        '''
        head_start_logits = self.head_start_metric_3(torch.tanh(
            self.head_start_metric_1(hidden_states).unsqueeze(2) + self.head_start_metric_2(
                encoder_hidden_states).unsqueeze(1))).squeeze()
        # bsz, q_num, enc_len, 1
        head_end_logits = self.head_end_metric_3(torch.tanh(
            self.head_end_metric_1(hidden_states).unsqueeze(2) + self.head_end_metric_2(
                encoder_hidden_states).unsqueeze(1))).squeeze()

        tail_start_logits = self.tail_start_metric_3(torch.tanh(
            self.tail_start_metric_1(hidden_states).unsqueeze(2) + self.tail_start_metric_2(
                encoder_hidden_states).unsqueeze(1))).squeeze()
        tail_end_logits = self.tail_end_metric_3(torch.tanh(
            self.tail_end_metric_1(hidden_states).unsqueeze(2) + self.tail_end_metric_2(
                encoder_hidden_states).unsqueeze(1))).squeeze()
        '''
        # sub_start_logits = logits[:, :, :, 0] # bsz, q_num, seq_len
        # sub_end_logits = logits[:, :, :, 1] # bsz, q_num, seq_len
        # obj_start_logits = logits[:, :, :, 2]
        # obj_end_logits = logits[:, :, :, 3]
        if self.args.multi_heads == 5: # 如果使用5个头预测results,则前面的是作为占位符，直接取后四位作为结果即可
            aspect_start_logits = logits[:, :, :, 4]
            aspect_end_logits = logits[:, :, :, 5]
            opinion_start_logits = logits[:, :, :, 6]
            opinion_end_logits = logits[:, :, :, 7]
        else: # 如果使用3个头预测results, 则没有空余占位符
            aspect_start_logits = logits[:, :, :, 0]
            aspect_end_logits = logits[:, :, :, 1]
            opinion_start_logits = logits[:, :, :, 2]
            opinion_end_logits = logits[:, :, :, 3]

        return hidden_states, class_logits, aspect_start_logits, aspect_end_logits, opinion_start_logits, opinion_end_logits


class DecoderLayer(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.attention = BertAttention(config)
        self.crossattention = BertAttention(config)
        self.intermediate = BertIntermediate(config)
        self.output = BertOutput(config) # FFN:  BertIntermediate + BertOutput

    def forward(
        self,
        hidden_states,
        encoder_hidden_states,
        encoder_attention_mask
    ):
        # hidden_states: bsz, q_num, hidden
        self_attention_outputs = self.attention(hidden_states)
        attention_output = self_attention_outputs[0]
        # bsz, q_num, q_num
        outputs = self_attention_outputs[1:]  # add self attentions if we output attention weights

        encoder_batch_size, encoder_sequence_length, _ = encoder_hidden_states.size()
        encoder_hidden_shape = (encoder_batch_size, encoder_sequence_length)
        if encoder_attention_mask.dim() == 3:
            encoder_extended_attention_mask = encoder_attention_mask[:, None, :, :]
        elif encoder_attention_mask.dim() == 2:
            encoder_extended_attention_mask = encoder_attention_mask[:, None, None, :]
        else:
            raise ValueError(
                "Wrong shape for encoder_hidden_shape (shape {}) or encoder_attention_mask (shape {})".format(
                    encoder_hidden_shape, encoder_attention_mask.shape
                )
            )
        encoder_extended_attention_mask = (1.0 - encoder_extended_attention_mask) * -10000.0
        cross_attention_outputs = self.crossattention(
            hidden_states=attention_output, encoder_hidden_states=encoder_hidden_states,  encoder_attention_mask=encoder_extended_attention_mask
        )
        attention_output = cross_attention_outputs[0]
        outputs = outputs + cross_attention_outputs[1:]  # add cross attentions if we output attention weights

        intermediate_output = self.intermediate(attention_output)
        layer_output = self.output(intermediate_output, attention_output)
        # bsz, q_num, hidden
        outputs = (layer_output,) + outputs
        return outputs