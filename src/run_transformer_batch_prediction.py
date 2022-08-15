# -*- coding: utf-8 -*-

"""
The input files must have offset information. In input file, for each word in line, it must have at least text, start, end, tag information
output file suffix will be set to .bio.txt
"""

import argparse
import os
import traceback
from pathlib import Path
from collections import defaultdict
import torch
import transformers
from packaging import version

from common_utils.common_io import json_load, output_bio
from common_utils.common_log import LOG_LVLs
from common_utils.output_format_converter import main as format_converter
from transformer_ner.data_utils import (TransformerNerDataProcessor,
                                        transformer_convert_data_to_features)
from transformer_ner.task import (MODEL_CLASSES, _output_bio, load_model,
                                  predict)
from transformer_ner.transfomer_log import TransformerNERLogger

pytorch_version = version.parse(transformers.__version__)
assert pytorch_version >= version.parse('3.0.0'), \
    'we now only support transformers version >=3.0.0, but your version is {}'.format(pytorch_version)


def main(args):
    label2idx = json_load(os.path.join(args.pretrained_model, "label2idx.json"))
    num_labels = len(label2idx)
    idx2label = {v: k for k, v in label2idx.items()}
    args.label2idx = label2idx
    args.idx2label = idx2label

    # get config, model and tokenizer
    model_config, _, model_tokenizer = MODEL_CLASSES[args.model_type]
    tokenizer = model_tokenizer.from_pretrained(args.pretrained_model, do_lower_case=args.do_lower_case)
    args.tokenizer = tokenizer

    config = model_config.from_pretrained(args.pretrained_model, do_lower_case=args.do_lower_case)
    args.config = config
    args.use_crf = config.use_crf

    model = load_model(args, args.pretrained_model)
    model.to(args.device)

    ner_data_processor = TransformerNerDataProcessor()
    ner_data_processor.set_logger(args.logger)
    ner_data_processor.set_data_dir(args.preprocessed_text_dir)
    if args.data_has_offset_information:
        ner_data_processor.offset_info_available()

    # fids = [each.stem.split(".")[0] for each in Path(args.preprocessed_text_dir).glob("*.txt")]
    labeled_bio_tup_lst = defaultdict(dict)
    for i, each_file in enumerate(Path(args.preprocessed_text_dir).glob("*.txt")):
        try:
            test_example = ner_data_processor.get_test_examples(file_name=each_file.name, use_bio=args.use_bio) #[(nsent, offsets, labels)]
            test_features = transformer_convert_data_to_features(args=args,
                                                                 input_examples=test_example,
                                                                 label2idx=label2idx,
                                                                 tokenizer=tokenizer,
                                                                 max_seq_len=args.max_seq_length)
            predictions = predict(args, model, test_features)
            
            if args.use_bio:
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)
            ofn = each_file.stem.split(".")[0] + ".bio.txt"
            args.predict_output_file = os.path.join(args.output_dir, ofn)
            _output_bio(args, test_example, predictions)
            else:
                labeled_bio_tup_lst[each_file.name]['sents'] = _output_bio(args, test_example, predictions, save_bio=False)
                with open(each_file, "r") as f:
                    labeled_bio_tup_lst[each_file.name]['raw_text'] = f.read()
        except Exception as ex:
            args.logger.error(f"Encountered an error when processing predictions for file: {each_file.name}")
            args.logger.error(traceback.format_exc())

    if args.do_format:
        output_formatted_dir = Path(args.output_dir_brat) if args.output_dir_brat else Path(args.output_dir).parent / "{}_formatted_output".format(Path(args.output_dir).stem)  
        output_formatted_dir.mkdir(parents=True, exist_ok=True)
        format_converter(text_dir=args.raw_text_dir,
                         input_bio_dir=(args.output_dir if args.use_bio else args.raw_text_dir),
                         output_dir=output_formatted_dir,
                         formatter=args.do_format,
                         do_copy_text=args.do_copy,
                         labeled_bio_tup_lst=labeled_bio_tup_lst,
                         use_bio=args.use_bio)

def argparser(args=None):
    parser = argparse.ArgumentParser()

    parser.add_argument("--model_type", default='bert', type=str, required=True,
                        help="valid values: bert, roberta or xlnet, albert, distilbert")
    parser.add_argument("--pretrained_model", type=str, required=True,
                        help="The pretrained model file or directory for fine tuning.")
    parser.add_argument("--preprocessed_text_dir", type=str, required=True,
                        help="The input data directory (bio with dummy label).")
    parser.add_argument("--raw_text_dir", type=str, required=True,
                        help="The input data directory (encoded text).")
    parser.add_argument("--data_has_offset_information", action='store_true',
                        help="Whether data has offset information.")
    parser.add_argument("--output_dir", type=str, required=True,
                        help="The output data directory (labeled bio).")
    parser.add_argument("--output_dir_brat", type=str,  default=None,
                        help="The output data directory (brat). Default: output_dir.parent / 'output_dir.stem'_formatted_output")
    parser.add_argument("--do_lower_case", action='store_true',
                        help="Set this flag if you are using an uncased model.")
    parser.add_argument("--eval_batch_size", default=8, type=int,
                        help="Total batch size for eval.")
    parser.add_argument("--max_seq_length", default=128, type=int,
                        help="maximum number of tokens allowed in each sentence")
    parser.add_argument("--log_file", default=None,
                        help="where to save the log information")
    parser.add_argument("--log_lvl", default="i", type=str,
                        help="d=DEBUG; i=INFO; w=WARNING; e=ERROR")
    parser.add_argument("--do_format", default=0, type=int,
                        help="0=bio (not format change will be applied); 1=brat; 2=bioc")
    parser.add_argument("--do_copy", action='store_true',
                        help="if copy the original plain text to output folder")
    parser.add_argument("--progress_bar", action='store_true',
                        help="show progress during the training in tqdm")
    parser.add_argument("--use_bio", action='store_true', default=False,
                        help="whether to use orignial text as input")
    parser.add_argument("--gpu_nodes", nargs="+", default=None,
                        help="use multiple gpu nodes")
    
    if args is None:
        return parser.parse_args()
    else:
        return parser.parse_args(args)


if __name__ == '__main__':
    global_args = argparser()
    
    # create logger
    logger = TransformerNERLogger(global_args.log_file, global_args.log_lvl).get_logger()
    global_args.logger = logger
    # device
    global_args.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Task will use cuda device: GPU_{}.".format(torch.cuda.current_device())
                if torch.cuda.device_count() else 'Task will use CPU.')

    main(global_args)
