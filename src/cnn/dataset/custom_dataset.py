import os
import pickle
import random

import pandas as pd
import numpy as np
import torch
import cv2
import pydicom
from pathlib import Path

from fastai2.basics           import *
from fastai2.vision.all       import *
from fastai2.medical.imaging  import *
from fastai2.callback.tracker import *

from .. import factory
from ..utils.logger import log
from ...utils import mappings, misc


def apply_window_policy(image, dicom, policy, bins):
    if policy == 1:
        image1 = misc.apply_window(image, 40, 80)  # brain
        image2 = misc.apply_window(image, 80, 200)  # subdural
        image3 = misc.apply_window(image, row.WindowCenter, row.WindowWidth)
        image1 = (image1 - 0) / 80
        image2 = (image2 - (-20)) / 200
        image3 = (image3 - image3.min()) / (image3.max()-image3.min())
        image = np.array([
            image1 - image1.mean(),
            image2 - image2.mean(),
            image3 - image3.mean(),
        ]).transpose(1, 2, 0)
    elif policy == 2:
        image1 = misc.apply_window(image, 40, 80)  # brain
        image2 = misc.apply_window(image, 80, 200)  # subdural
        image3 = misc.apply_window(image, 40, 380)  # bone
        image1 = (image1 - 0) / 80
        image2 = (image2 - (-20)) / 200
        image3 = (image3 - (-150)) / 380
        image = np.array([
            image1 - image1.mean(),
            image2 - image2.mean(),
            image3 - image3.mean(),
        ]).transpose(1, 2, 0)
    elif policy == 3:
        image1 = misc.apply_window(image, 40, 80)  # brain
        image2 = misc.apply_window(image, 80, 200)  # subdural
        image3 = np.array(dicom.scaled_px.hist_scaled(bins))
        if image3.shape != (512,512):
            image3 = misc.apply_window(image, 40, 380) #bone
        image1 = (image1 - 0) / 80
        image2 = (image2 - (-20)) / 200
        image3 = (image3 - image3.min()) / (image3.max()-image3.min())
        image = np.array([
            image1 - image1.mean(),
            image2 - image2.mean(),
            image3 - image3.mean(),
        ]).transpose(1, 2, 0)
    else:
        raise

    return image


def apply_dataset_policy(df, policy):
    if policy == 'all':
        pass
    elif policy == 'pos==neg':
        df_positive = df[df.labels != '']
        df_negative = df[df.labels == '']
        df_sampled = df_negative.sample(len(df_positive))
        df = pd.concat([df_positive, df_sampled], sort=False)
    else:
        raise
    log('applied dataset_policy %s (%d records)' % (policy, len(df)))

    return df


class CustomDataset(torch.utils.data.Dataset):

    def __init__(self, cfg, folds):
        self.cfg = cfg

        log(f'dataset_policy: {self.cfg.dataset_policy}')
        log(f'window_policy: {self.cfg.window_policy}')

        self.transforms = factory.get_transforms(self.cfg)
        with open(cfg.annotations, 'rb') as f:
            self.df = pickle.load(f)

        with open(cfg.bins, 'rb') as f:
            self.bins = pickle.load(f)

        if folds:
            self.df = self.df[self.df.fold.isin(folds)]
            log('read dataset (%d records)' % len(self.df))

        self.df = apply_dataset_policy(self.df, self.cfg.dataset_policy)
        #self.df = self.df.sample(560)

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]

        path = Path('%s/%s.dcm' % (self.cfg.imgdir, row.ID))

        dicom = path.dcmread()
        image = (dicom.pixel_array).astype(np.float32)
        image = misc.rescale_image(
            image, row.RescaleSlope, row.RescaleIntercept)
        image = apply_window_policy(
            image, dicom, self.cfg.window_policy, self.bins)

        image = self.transforms(image=image)['image']

        target = np.array([0.0] * len(mappings.label_to_num))
        for label in row.labels.split():
            cls = mappings.label_to_num[label]
            target[cls] = 1.0

        if hasattr(self.cfg, 'spread_diagnosis'):
            for label in row.LeftLabel.split() + row.RightLabel.split():
                cls = mappings.label_to_num[label]
                target[cls] += self.cfg.propagate_diagnosis
        target = np.clip(target, 0.0, 1.0)

        return image, torch.FloatTensor(target), row.ID
