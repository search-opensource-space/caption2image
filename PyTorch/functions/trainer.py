#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  7 15:45:31 2018

@author: danny
"""
from minibatchers import iterate_tokens_5fold, iterate_char_5fold, iterate_audio_5fold, iterate_snli_tokens, iterate_snli
from grad_tracker import gradient_clipping
from evaluate import evaluate

import numpy as np
from torch.autograd import Variable
import torch
import os
import time

# trainer for the flickr database. 
class flickr_trainer():
    def __init__(self, img_embedder, cap_embedder, vis, cap):
        # default datatype, change to cuda by calling set_cuda
        self.dtype = torch.FloatTensor
        # set the embedders. Set an empty scheduler to keep this optional.
        self.img_embedder = img_embedder
        self.cap_embedder = cap_embedder
        self.scheduler = False
        # set gradient clipping to false by default
        self.grad_clipping = False
        # set the attention loss to empty by default
        self.att_loss = False
        # names of the features to be loaded by the batcher
        self.vis = vis
        self.cap = cap
        # keep track of an iteration for lr scheduling
        self.iteration = 0
        # keep track of the number of training epochs
        self.epoch = 1
    # the possible minibatcher for all different types of data for the flickr database
    def token_batcher(self, data, batch_size, shuffle):
        return iterate_tokens_5fold(data, batch_size, self.vis, self.cap, self.dict_loc, shuffle)
    def audio_batcher(self, data, batch_size, shuffle):
        return iterate_audio_5fold(data, batch_size, self.vis, self.cap, shuffle)
    def raw_text_batcher(self, data, batch_size, shuffle):
        return iterate_char_5fold(data, batch_size, self.vis, self.cap, shuffle)    

################## functions to set class values and attributes ###############
    # functions to set which minibatcher to use. Needs to be called as no default is set.
    def set_token_batcher(self):
        self.batcher = self.token_batcher
    def set_raw_text_batcher(self):
        self.batcher = self.raw_text_batcher
    def set_audio_batcher(self):
        self.batcher = self.audio_batcher
    # function to set the learning rate scheduler
    def set_lr_scheduler(self, scheduler, s_type):
        self.lr_scheduler = scheduler  
        self.scheduler = s_type
    # function to set the loss for training. Loss is not necessary e.g. when you 
    # only want to test a pretrained model.
    def set_loss(self, loss):
        self.loss = loss
    # loss function on the attention layer for multihead attention
    def set_att_loss(self, att_loss):
        self.att_loss = att_loss
    # set an optimizer. Optional like the loss in case of using just pretrained models.
    def set_optimizer(self, optim):
        self.optimizer = optim
    # set a dictionary for models trained on tokens
    def set_dict_loc(self, loc):
        self.dict_loc = loc
    # set data type and the networks to cuda
    def set_cuda(self):
        self.dtype = torch.cuda.FloatTensor
        self.img_embedder.cuda()
        self.cap_embedder.cuda()
    # manually set the epoch to some number e.g. if continuing training from a 
    # pretrained model
    def set_epoch(self, epoch):
        self.epoch = epoch
    def update_epoch(self):
        self.epoch += 1
    # functions to set new embedders
    def set_img_embedder(self, emb):
        self.img_embedder = emb
    def set_cap_embedder(self, emb):
        self.cap_embedder = emb
    # functions to load a pretrained embedder
    def load_cap_embedder(self, loc):
        cap_state = torch.load(loc)
        self.cap_embedder.load_state_dict(cap_state)
    def load_img_embedder(self, loc):
        img_state = torch.load(loc)
        self.img_embedder.load_state_dict(img_state)
    # optionally load glove embeddings for token based embedders with load_embeddings
    # function implemented.
    def load_glove_embeddings(self, glove_loc):
        self.cap_embedder.load_embeddings(self.dict_loc, glove_loc)
       
################## functions to perform training and testing ##################
    def train_epoch(self, data, batch_size):
        print('training epoch: ' + str(self.epoch))
        # keep track of runtime
        self.start_time = time.time()
        self.img_embedder.train()
        self.cap_embedder.train()
        # for keeping track of the average loss over all batches
        self.train_loss = 0
        num_batches = 0
        for batch in self.batcher(data, batch_size, shuffle = True):
            # retrieve a minibatch from the batcher
            img, cap, lengths = batch
            num_batches +=1
            # embed the images and audio using the networks
            img_embedding, cap_embedding = self.embed(img, cap, lengths)
            # calculate the loss
            loss = self.loss(img_embedding, cap_embedding, self.dtype)
            # optionally calculate the attention loss for multihead attention
            if self.att_loss:
                loss += self.att_loss(self.cap_embedder.att, cap_embedding)
            # reset the gradients of the optimiser
            self.optimizer.zero_grad()
            # calculate the gradients and perform the backprop step
            loss.backward()
            # clip the gradients if required
            if self.grad_clipping:
                torch.nn.utils.clip_grad_norm(self.img_embedder.parameters(), self.img_clipper.clip)
                torch.nn.utils.clip_grad_norm(self.cap_embedder.parameters(), self.cap_clipper.clip)
            # update weights
            self.optimizer.step()
            # add loss to average
            self.train_loss += loss.data
            # print loss every n batches
            if num_batches%100 == 0:
                print(self.train_loss.cpu()[0].data.numpy()/num_batches)
            # if there is a lr scheduler, take a step in the scheduler
            if self.scheduler == 'cyclic':
                self.lr_scheduler.step()
                self.iteration +=1
        self.train_loss = self.train_loss.cpu()[0].data.numpy()/num_batches
    
    def test_epoch(self, data, batch_size):
        # set to evaluation mode
        self.img_embedder.eval()
        self.cap_embedder.eval()
        # for keeping track of the average loss
        test_batches = 0
        self.test_loss = 0
        for batch in self.batcher(data, batch_size, shuffle = False):
            img, cap, lengths = batch
            test_batches += 1      
            # embed the images and audio using the networks
            img_embedding, cap_embedding = self.embed(img, cap, lengths)
            loss = self.loss(img_embedding, cap_embedding, self.dtype)
            if self.att_loss:
                loss += self.att_loss(self.cap_embedder.att, cap_embedding)
            # add loss to average
            self.test_loss += loss.data 
        self.test_loss = self.test_loss.cpu()[0].data.numpy()/test_batches
        # take a step for a plateau lr scheduler                
        if self.scheduler == 'plateau':
            self.lr_scheduler.step(self.test_loss)    
    # embed a batch of images and captions
    def embed(self, img, cap, lengths):
        # sort the tensors based on the unpadded caption length so they can be used
        # with the pack_padded_sequence function
        cap = cap[np.argsort(- np.array(lengths))]
        img = img[np.argsort(- np.array(lengths))]
        lengths = np.array(lengths)[np.argsort(- np.array(lengths))]     
        # convert data to pytorch variables
        img, cap = Variable(self.dtype(img)), Variable(self.dtype(cap))        
        # embed the images and audio using the networks
        img_embedding = self.img_embedder(img)
        cap_embedding = self.cap_embedder(cap, lengths)
        return img_embedding, cap_embedding
######################## evaluation functions #################################
    # report on the time this epoch took and the train and test loss
    def report(self, max_epochs):
        # report on the time and train and val loss for the epoch
        print("Epoch {} of {} took {:.3f}s".format(
                self.epoch, max_epochs, time.time() - self.start_time))
        self.print_train_loss()
        self.print_validation_loss()
    # print the loss values
    def print_train_loss(self):  
        print("training loss:\t\t{:.6f}".format(self.train_loss))
    def print_test_loss(self):        
        print("test loss:\t\t{:.6f}".format(self.test_loss))
    def print_validation_loss(self):
        print("validation loss:\t\t{:.6f}".format(self.test_loss))
    # create and manipulate an evaluator object   
    def set_evaluator(self, n):
        self.evaluator = evaluate(self.dtype, self.img_embedder, self.cap_embedder)
        self.evaluator.set_n(n)
    # calculate the recall@n. Arguments are a set of nodes and a prepend string 
    # (e.g. to print validation or test in front of the results)
    def recall_at_n(self, data, batch_size, prepend):        
        iterator = self.batcher(data, 5, shuffle = False)
        # the calc_recall function calculates and prints the recall.
        self.evaluator.embed_data(iterator)
        self.evaluator.print_caption2image(prepend, self.epoch)
        self.evaluator.print_image2caption(prepend, self.epoch)
    def fivefold_recall_at_n(self, prepend):
        # calculates the average recall@n over 5 folds (for mscoco). 
        self.evaluator.fivefold_c2i('1k ' + prepend, self.epoch)
        self.evaluator.fivefold_i2c('1k ' + prepend, self.epoch)
    # function to save parameters in a results folder
    def save_params(self, loc):
        torch.save(self.cap_embedder.state_dict(), os.path.join(loc, 'caption_model' + '.' +str(self.epoch)))
        torch.save(self.img_embedder.state_dict(), os.path.join(loc, 'image_model' + '.' +str(self.epoch)))

############ functions to deal with the trainer's gradient clipper ############
    # create a gradient tracker/clipper
    def set_gradient_clipping(self, img_clip_value, cap_clip_value):
        self.grad_clipping = True
        self.img_clipper = gradient_clipping(img_clip_value)
        self.cap_clipper = gradient_clipping(cap_clip_value)  
        self.img_clipper.register_hook(self.img_embedder)
        self.cap_clipper.register_hook(self.cap_embedder)
    # save the gradients collected so far 
    def save_gradients(self, loc):
        self.cap_clipper.save_grads(loc, 'cap_grads')
        self.img_clipper.save_grads(loc, 'img_grads')
    # reset the grads for a new epoch
    def reset_grads(self):
        self.cap_clipper.reset_gradients()
        self.img_clipper.reset_gradients()
    # update the clip value of the gradient clipper based on the previous epoch. Don't call after resetting
    # the grads to 0
    def update_clip(self):
        self.cap_clipper.update_clip_value()
        self.img_clipper.update_clip_value()

# trainer for the snli entailment task 
class snli_trainer():
    def __init__(self, cap_embedder, classifier):
        self.dtype = torch.FloatTensor
        self.long = torch.LongTensor
        self.cap_embedder = cap_embedder
        self.classifier = classifier
        self.scheduler = False
        # set gradient clipping to false by default
        self.grad_clipping = False
        self.att_loss = False
        # keep track of an iteration for lr scheduling
        self.iteration = 0
        # keep track of the number of training epochs
        self.epoch = 1
        
    def token_batcher(self, data, batch_size, shuffle):
        return iterate_snli_tokens(data, batch_size, self.dict_loc, shuffle)
    def raw_text_batcher(self, data, batch_size, shuffle):
        return iterate_snli(data, batch_size, shuffle)  

################## functions to set class values and attributes ###############
    # function to set the loss for training. Loss is not necessary e.g. when you 
    # only want to test a pretrained model.
    def set_loss(self, loss):
        self.loss = loss
    # loss function on the attention layer for multihead attention
    def set_att_loss(self, att_loss):
        self.att_loss = att_loss
    # set an optimizer. Optional like the loss in case of using just pretrained models.
    def set_optimizer(self, optim):
        self.optimizer = optim        # take a step for a plateau lr scheduler                
        if self.scheduler == 'plateau':
            self.lr_scheduler.step(self.test_loss)
    # set a dictionary for models trained on tokens
    def set_dict_loc(self, loc):
        self.dict_loc = loc    
    # functions to set which minibatcher to use
    def set_token_batcher(self):
        self.batcher = self.token_batcher
    def set_raw_text_batcher(self):
        self.batcher = self.raw_text_batcher
    # function to set the learning rate scheduler
    def set_lr_scheduler(self, scheduler, s_type):
        self.lr_scheduler = scheduler  
        self.scheduler = s_type
    def set_cuda(self):
        self.dtype = torch.cuda.FloatTensor
        self.long = torch.cuda.LongTensor
        self.cap_embedder.cuda()
        self.classifier.cuda()
    # manually set the epoch to some number e.g. if continuing training from a 
    # pretrained model
    def set_epoch(self, epoch):
        self.epoch = epoch
    def update_epoch(self):
        self.epoch += 1
    # functions to set new embedders
    def set_classifier(self, clas):
        self.classifier = clas
    def set_cap_embedder(self, emb):
        self.cap_embedder = emb
    # functions to load a pretrained embedder
    def load_classifier(self, loc):
        clas_state = torch.load(loc)
        self.classifier.load_state_dict(clas_state)
    def load_cap_embedder(self, loc):
        cap_state = torch.load(loc)
        self.cap_embedder.load_state_dict(cap_state)
    # optionally load glove embeddings for token based embedders with load_embeddings
    # function implemented.
    def load_glove_embeddings(self, glove_loc):
        self.cap_embedder.load_embeddings(self.dict_loc, glove_loc)