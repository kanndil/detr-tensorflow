import argparse
import matplotlib.pyplot as plt
import tensorflow as tf
import numpy as np
import time
import os
#from loss import 

#physical_devices = tf.config.list_physical_devices('GPU')
#for device in physical_devices:
#    tf.config.experimental.set_memory_growth(device, True)

#tf.debugging.set_log_device_placement(True)

from data.coco import load_coco, CLASS_NAME
from networks.detr import get_detr_model
from optimizers import setup_optimizers
from optimizers import gather_gradient, aggregate_grad_and_apply
from logger.training_logging import train_log, valid_log
from loss.loss import get_losses
from inference import numpy_bbox_to_image
from training_config import TrainingConfig

parser = argparse.ArgumentParser()

# Dataset info
parser.add_argument("--cocodir",  type=str, required=True, help="/path/to/coco")
parser.add_argument("--background_class",  type=int, required=False, default=91, help="Default background class")

# What to train
parser.add_argument("--train_backbone", action='store_true',  required=False, default=False, help="Train backbone")
parser.add_argument("--train_transformers", action='store_true',   required=False, default=False, help="Train transformers")
parser.add_argument("--train_nlayers",  action='store_true',  required=False, default=False, help="Train new layers")

# How to train
parser.add_argument("--finetuning",  default=False, required=False, action='store_true', help="Load the model weight before to train")
parser.add_argument("--load_backbone",  default=False, required=False, action='store_true', help="Load weights from the backbone only (testing purposes)")
parser.add_argument("--batch_size",  type=int, required=False, default=False, help="Batch size to use to train the model")
parser.add_argument("--gradient_norm_clipping",  type=float, required=False, default=0.1, help="Gradient norm clipping")
parser.add_argument("--target_batch",  type=int, required=False, default=None, help="When running on a single GPU, aggretate the gradient before to apply.")

# Learning rate
parser.add_argument("--backbone_lr",  type=bool, required=False, default=1e-5, help="Train backbone")
parser.add_argument("--transformers_lr",  type=bool, required=False, default=1e-4, help="Train transformers")
parser.add_argument("--nlayers_lr",  type=bool, required=False, default=1e-4, help="Train new layers")

# Logging
parser.add_argument("--log",  required=False, action="store_true", default=False, help="Log into wandb")

import wandb

def args_to_config(args):
    args = vars(args)
    config = TrainingConfig()
    for key in args:
        setattr(config, key, args[key])
    return config


@tf.function
def run_train_step(model, images, t_bbox, t_class, optimizers, config):

    if config.target_batch is not None:
        gradient_aggregate = int(config.target_batch // config.batch_size)
    else:
        gradient_aggregate = 1

    with tf.GradientTape() as tape:
        m_outputs = model(images, training=True)
        total_loss, log = get_losses(m_outputs, t_bbox, t_class, config)
        total_loss = total_loss / gradient_aggregate

    # Compute gradient for each part of the network
    gradient_steps = gather_gradient(model, optimizers, total_loss, tape, config, log)

    return m_outputs, total_loss, log, gradient_steps


@tf.function
def run_val_step(model, images, t_bbox, t_class, config):
    m_outputs = model(images, training=False)
    total_loss, log = get_losses(m_outputs, t_bbox, t_class, config)
    return m_outputs, total_loss, log


def run_epoch(model, train_dt, optimizers, config, epoch_nb):


    gradient_aggregate = None
    if config.target_batch is not None:
        gradient_aggregate = int(config.target_batch // config.batch_size)

    t = None
    for epoch_step , (images, t_bbox, t_class) in enumerate(train_dt):
        
        m_outputs, total_loss, log, gradient_steps = run_train_step(model, images, t_bbox, t_class, optimizers, config)
        # Training log
        train_log(images, t_bbox, t_class, m_outputs, config, config.global_step,  CLASS_NAME, prefix="train/")

        for name in gradient_steps:
            aggregate_grad_and_apply(name, optimizers, gradient_steps[name]["gradients"], epoch_step, config)

        if epoch_step % 100 == 0:
            t = t if t is not None else time.time()
            elapsed = time.time() - t
            print(f"Epoch: [{epoch_nb}], \t Step: [{epoch_step}], \t ce: [{log['label_cost']:.2f}] \t giou : [{log['giou_loss']:.2f}] \t l1 : [{log['l1_loss']:.2f}] \t time : [{elapsed:.2f}]")
            wandb.log({f"train/{k}":log[k] for k in log}, step=config.global_step)
            t = time.time()
        
        config.global_step += 1


def run_validation(model, valid_dt, optimizers, config, evaluation_step=200):

    gradient_aggregate = None
    if config.target_batch is not None:
        gradient_aggregate = int(config.target_batch // config.batch_size)

    t = None
    for val_step, (images, t_bbox, t_class) in enumerate(valid_dt):

        m_outputs, total_loss, log = run_val_step(model, images, t_bbox, t_class, config)
        valid_log(images, t_bbox, t_class, m_outputs, config, val_step, config.global_step,  CLASS_NAME, evaluation_step=evaluation_step, prefix="train/")

        if val_step == 0:
            wandb.log({f"val/{k}":log[k] for k in log}, step=config.global_step)
        
        if val_step % 10 == 0:
            t = t if t is not None else time.time()
            elapsed = time.time() - t
            print(f"Validation step: [{val_step}], \t ce: [{log['label_cost']:.2f}] \t giou : [{log['giou_loss']:.2f}] \t l1 : [{log['l1_loss']:.2f}] \t time : [{elapsed:.2f}]")
            
        if val_step+1 >= evaluation_step:
            break
        
    #wandb.log(log, step=config.global_step)


def run_training(args):

    config = args_to_config(args)
    config.normalized_method = "tf_resnet50"

    model = get_detr_model(finetuning=config.finetuning, load_backbone=config.load_backbone)

    # Load the training dataset
    train_dt = load_coco(args.cocodir, "val", config.batch_size, config, augmentation=True)
    # Load the validation dataset
    valid_dt = load_coco(args.cocodir, "val", 1, config)

    # Setup the optimziers and the trainable variables
    optimzers = setup_optimizers(model, args)

    global_step = 0
    for e in range(300):
        run_validation(model, valid_dt, optimzers, config, evaluation_step=200)
        step = run_epoch(model, train_dt, optimzers, config, e)

if __name__ == "__main__":
    args = parser.parse_args()
    # Init wandb logging
    wandb_mode = "" if args.log else "dryrun"

    import os
    os.environ["WANDB_MODE"] = wandb_mode
    wandb.init(project="detr-tensorflow", reinit=True)

    # Run training
    run_training(args)




