import imageio
import imgaug as ia
import imgaug.augmenters as iaa
import numpy as np

from imgaug.augmentables.bbs import BoundingBox, BoundingBoxesOnImage
from imgaug.augmentables.segmaps import SegmentationMapsOnImage

import tensorflow as tf
# > 2.3
if int(tf.__version__.split('.')[1]) > 3:
    RAGGED = True
else:
    RAGGED = False


def bbox_xcyc_wh_to_imgaug_bbox(bbox, target_class, height, width):
    nb_bbox = bbox[0][0]

    img_aug_bbox = []
    
    if not RAGGED:
        nb_bbox = int(bbox[0][0])
        bbox = bbox[1:nb_bbox+1]
        target_class = target_class[1:nb_bbox+1]

    for b in range(0, len(bbox)):
        bbox_xcyc_wh = bbox[b]
        # Convert size form 0.1 to height/width
        bbox_xcyc_wh = [
            bbox_xcyc_wh[0] * width,
            bbox_xcyc_wh[1] * height,
            bbox_xcyc_wh[2] * width,
            bbox_xcyc_wh[3] * height
        ]
        x1 = bbox_xcyc_wh[0] - (bbox_xcyc_wh[2] / 2)
        x2 = bbox_xcyc_wh[0] + (bbox_xcyc_wh[2] / 2)
        y1 = bbox_xcyc_wh[1] - (bbox_xcyc_wh[3] / 2)
        y2 = bbox_xcyc_wh[1] + (bbox_xcyc_wh[3] / 2)

        n_bbox = BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2, label=target_class[b])

        img_aug_bbox.append(n_bbox)
    img_aug_bbox
    return img_aug_bbox


def prepare_aug_inputs(image, bbox, t_class):

    images_batch = []
    bbox_batch = []

    images_batch.append(image)

    # Create the Imgaug bbox
    bbs_original = bbox_xcyc_wh_to_imgaug_bbox(bbox, t_class, image.shape[0], image.shape[1])
    bbs_original = BoundingBoxesOnImage(bbs_original, shape=image.shape)
    bbox_batch.append(bbs_original)

    for i in range(len(images_batch)):
        images_batch[i] = images_batch[i].astype(np.uint8)
    
    return images_batch, bbox_batch


def detr_aug_seq(image, augmenation):


    sometimes = lambda aug: iaa.Sometimes(0.5, aug)

    target_min_side_size = 480

    # According to the paper
    min_side_min = 480
    min_side_max = 800 
    max_side_max = 1333

    if augmenation:

        # Fixe size mode
        image_size = 376, 672
        seq = iaa.Sequential([
            iaa.Fliplr(0.5), # horizontal flips
            sometimes(iaa.OneOf([
                # Resize complety the image
                iaa.Resize({"width": image_size[1], "height": image_size[0]}, interpolation=ia.ALL),
                # Crop into the image
                iaa.CropToFixedSize(image_size[1], image_size[0]),
                # Affine transform
                iaa.Affine(
                    scale={"x": (0.5, 1.5), "y": (0.5, 1.5)}, 
                )
            ])),
            # Be sure to resize to the target image size
            iaa.Resize({"width": image_size[1], "height": image_size[0]}, interpolation=ia.ALL)
        ], random_order=False) # apply augmenters in random order
    
        return seq

    else:

        # Fixe size mode
        image_size = 376, 672
        seq = iaa.Sequential([
            # Be sure to resize to the target image size
            iaa.Resize({"width": image_size[1], "height": image_size[0]})
        ], random_order=False) # apply augmenters in random order
    
        return seq

        """ Mode paper evaluation
        # Evaluation mode, we took the largest min side the model is trained on
        target_min_side_size = 480 
        image_min_side = min(float(image.shape[0]), float(image.shape[1]))
        image_max_side = max(float(image.shape[0]), float(image.shape[1]))
        
        min_side_scaling = target_min_side_size / image_min_side
        max_side_scaling = max_side_max / image_max_side
        scaling = min(min_side_scaling, max_side_scaling)

        n_height = int(scaling * image.shape[0])
        n_width = int(scaling * image.shape[1])

        seq = iaa.Sequential([
            iaa.Resize({"height": n_height, "width": n_width}),
        ])
        """

    return seq


def imgaug_bbox_to_xcyc_wh(bbs_aug, height, width):

    if RAGGED:
        bbox_xcyc_wh = []
        t_class = []
    else:
        bbox_xcyc_wh = np.zeros((100, 4))
        t_class = np.zeros((100, 1))

    nb_bbox = 0

    for b, bbox in enumerate(bbs_aug):

        h = bbox.y2 - bbox.y1
        w = bbox.x2 - bbox.x1
        xc = bbox.x1 + (w/2)
        yc = bbox.y1 + (h/2)
 
        assert bbox.label != None

        if RAGGED:
            bbox_xcyc_wh.append([xc / width, yc / height, w / width, h / height])
            t_class.append(bbox.label)
        else:
            bbox_xcyc_wh[b+1] = [xc / width, yc / height, w / width, h / height]
            t_class[b+1] = [bbox.label]
        
        nb_bbox += 1

    bbox_xcyc_wh[0][0] = nb_bbox
    bbox_xcyc_wh = np.array(bbox_xcyc_wh)

    return bbox_xcyc_wh, t_class


def retrieve_outputs(augmented_images, augmented_bbox):

    outputs_dict = {}
    image_shape = None


    # We expect only one image here for now
    image = augmented_images[0].astype(np.float32)
    augmented_bbox = augmented_bbox[0]

    bbox, t_class = imgaug_bbox_to_xcyc_wh(augmented_bbox, image.shape[0], image.shape[1])

    return image, bbox, t_class



def detr_aug(image, bbox, t_class, augmentation):

    #print("1) bbox", bbox)
    # Prepare the augmenation input pipeline
    images_batch, bbox_batch = prepare_aug_inputs(image, bbox, t_class)
    #print("2) bbox_batch", bbox_batch)

    seq = detr_aug_seq(image, augmentation)

    # Run the pipeline in a deterministic manner
    seq_det = seq.to_deterministic()

    augmented_images = []
    augmented_bbox = []
    augmented_class = []

    for img, bbox, t_cls in zip(images_batch, bbox_batch, t_class):

        img_aug = seq_det.augment_image(img)
        bbox_aug = seq_det.augment_bounding_boxes(bbox)

        #print("3) bbox_aug", bbox_aug)

        for b, bbox_instance in enumerate(bbox_aug.items):
            setattr(bbox_instance, "instance_id", b+1)

        bbox_aug = bbox_aug.remove_out_of_image_fraction(0.7)
        segmap_aug = None
        bbox_aug = bbox_aug.clip_out_of_image()

        #print("4) bbox_aug", bbox_aug)

        augmented_images.append(img_aug)
        augmented_bbox.append(bbox_aug)

    return retrieve_outputs(augmented_images, augmented_bbox)

