from __future__ import print_function
import sys
import itertools
import cv2
import numpy as np
from random import randint
import logging
from prettytable import PrettyTable
import os
import multiprocessing as MP
import shutil
import json
import time

from utils.utils import bbox_iou, bbox_distance


def create_tracker_by_type(tracker_type):
    # Create a tracker based on tracker name
    if tracker_type == 'BOOSTING':
        tracker = cv2.TrackerBoosting_create()
    elif tracker_type == 'MIL':
        tracker = cv2.TrackerMIL_create()
    elif tracker_type == 'KCF':
        tracker = cv2.TrackerKCF_create()
    elif tracker_type == 'TLD':
        tracker = cv2.TrackerTLD_create()
    elif tracker_type == 'MEDIANFLOW':
        tracker = cv2.TrackerMedianFlow_create()
    elif tracker_type == 'GOTURN':
        tracker = cv2.TrackerGOTURN_create()
    elif tracker_type == 'MOSSE':
        tracker = cv2.TrackerMOSSE_create()
    elif tracker_type == 'CSRT':
        tracker = cv2.TrackerCSRT_create()
        # Todo - not hardcoded and add checks
        fs_settings = cv2.FileStorage("./trackers/cfg/csrt_settings.yaml", cv2.FILE_STORAGE_READ)
        tracker.read(fs_settings.root())
    else:
        raise Exception(f"There is no tracker name: {tracker_type}")

    return tracker

# class Object(MP.Process):
class Object(object):
    def __init__(self, id, frame, class_type, bbox, tracker_type, xyxy=True):
        super(Object, self).__init__()
        self.id = id
        self.class_type = class_type
        if xyxy:
            w, h = round(bbox[2] - bbox[0]), round(bbox[3] - bbox[1])
        else:
            w, h = int(bbox[2]), int(bbox[3])
        self.bbox = (int(bbox[0]),int(bbox[1]), w, h)
        self.color = (randint(0, 255), randint(0, 255), randint(0, 255))
        self.tracker_type = tracker_type
        self.frames_without_detection = 0
        self.tracker = create_tracker_by_type(tracker_type)
        success = self.tracker.init(frame, self.bbox)
        if not success:
            raise Exception("could not init tracker")

    def update(self, frame):
#         print("obj id ", self.id)
        ok, bbox = self.tracker.update(frame)
        if not ok:
            self.frames_without_detection += 1
        else:
            self.frames_without_detection = 0
            self.bbox = bbox
        return ok

    def reinitialize(self, frame, bbox):
        self.tracker = create_tracker_by_type(self.tracker_type)
        success = self.tracker.init(frame, bbox)
        if not success:
            raise Exception("could not init tracker")
        self.bbox = bbox
        self.frames_without_detection = 0        

    def get_metadata(self):
        return [self.id, self.class_type, self.bbox, self.tracker_type, self.frames_without_detection]


# class ParallelUpdates(MP.Process):
#     def __init__(self, obj, frame, queue):
#         super(ParallelUpdates, self).__init__()
#         self.obj = obj
#         self.frame = frame
#         self.queue = queue
#         self.ok = None
    
#     def run(self):
#         ok = self.obj.update(self.frame)
#         self.queue.put(ok)
        
        
class MultiTracker:
    def __init__(self, default_tracker='CSRT',
                failures_threshold=0.5,
                iou_thres=0.1,
                dist_thres=100,
                no_detection_thres=0,
                logger=logging.getLogger(),
                classes=None):
        
        self.objects = []
        self.default_tracker = default_tracker
        self.new_id = itertools.count().__next__
        self.failures_threshold = failures_threshold
        self.iou_thres = iou_thres
        self.dist_thres = dist_thres
        self.no_detection_thres = no_detection_thres
        self.logger = logger
        self.classes = classes
        self.object_history = {}

    def initialize(self, frame, detections, trackerType=None):
        if trackerType is None:
            trackerType = self.default_tracker
        self.logger.info("Initializing multi tracker")
        for det in detections:
            bbox = tuple(det[:4]) # conversion for opencv tracker
            class_type = int(det[5])
            obj = self.add(trackerType, frame, bbox, class_type)
        self.logger.info(f"Initial detections:\n {self.get_objects_metadata()}")
            
    def add(self, tracker_type, frame, bbox, class_type, xyxy=True):
        obj = Object(self.new_id(), frame, class_type, bbox, tracker_type, xyxy)
        self.objects.append(obj)
        return obj

#     def parallel_update(self, frame):
#         jobs = []
#         queue = MP.Queue()
#         for i, obj in enumerate(self.objects):
#             p = ParallelUpdates(obj, frame, queue)
#             jobs.append(p)
#             p.start()
            
#         [p.join() for p in jobs]
        
#         number_of_fails = 0
#         while not queue.empty():
#             ok = queue.get()
#             number_of_fails += 1 - int(ok)
#         fails_percentage = float(number_of_fails)/float(len(self.objects))
#         self.logger.debug(f"fails_percentage: {(fails_percentage*100):.2f}%")
#         if fails_percentage < self.failures_threshold:
#             return True
#         return False

    def track_history(self, frame_number):
        for i, obj in enumerate(self.objects):
            if obj.frames_without_detection == 0:
                # make points instead of xywh 
                x,y,w,h = obj.bbox
                current_loc = [(x,y),(x,y+h),(x+w,y+h),(x+w,y)]
            else:
                current_loc = [(None,None) for _ in range(4)]
            try:
                current_loc = [frame_number] + current_loc
                self.object_history[obj.id].append(current_loc)
            except KeyError:
                self.object_history[obj.id] = [current_loc]
    
    def write_history(self, output_folder):
        annotations_folder = os.path.join(output_folder, 'MultiTracker_annotations')
        if os.path.isdir(annotations_folder):
            shutil.rmtree(annotations_folder)
        os.mkdir(annotations_folder)
        for id, history in self.object_history.items():
            # print("ID, history", id, len(history))
            with open(os.path.join(annotations_folder, f'{id}.txt'), 'w') as f:
                json.dump(history, f)
            
    
    def update(self, frame):
        if not self.objects:
            return False
        number_of_fails = 0
        for i, obj in enumerate(self.objects):
            ok = obj.update(frame)
            number_of_fails += 1 - int(ok)
            # Todo - maybe remove objects here
        fails_percentage = float(number_of_fails)/float(len(self.objects))

        if fails_percentage < self.failures_threshold:
            return True
        return False

    def get_bboxes_ids(self):
        bboxes_ids = []
        for obj in self.objects:
            if obj.frames_without_detection == 0:
                bboxes_ids.append((obj.bbox, obj.id))
        return bboxes_ids

    def get_objects_metadata(self):
        objects_table = PrettyTable(["ID", "Type", "xywh", "Tracker", "FWD"])
        for obj in self.objects:
            objects_table.add_row(obj.get_metadata())
        return objects_table
    
    def new_detection(self, frame, detections, xywh=False):
        if detections is None or not len(detections):
            self.logger.info("No detections added")
            return
        detections = detections.cpu().numpy()
        if not xywh:
            detections[:, 2] = detections[:, 2] - detections[:, 0]
            detections[:, 3] = detections[:, 3] - detections[:, 1]
        to_remove = []
        self.logger.info("Adding new detections")
        for i, obj in enumerate(self.objects):
            if detections is None or not len(detections):
                return
            
            cur_bbox = np.array(obj.bbox)
            bboxes_array = detections[:,:4]
            ious = bbox_iou(cur_bbox, bboxes_array)
            relevant_idx = np.where(obj.class_type == detections[:, 5])[0]  # matching class objects          
            if relevant_idx.size == 0:
                continue

            greatest_overlap = relevant_idx[ious[relevant_idx].argmax()] # max overlap from matching objects
            # check for greatest intersection over union detection
            if ious[greatest_overlap] > self.iou_thres:
                self.logger.debug(f"Reinitialize Object id: {obj.id} of type: {obj.class_type} due large overlap")
                bbox = tuple(map(int, bboxes_array[greatest_overlap]))
#                 bbox = tuple(np.around(bboxes_array[greatest_overlap]).astype(int)) # conversion for opencv
                obj.reinitialize(frame, bbox)
                detections = np.delete(detections, greatest_overlap, axis=0)
                continue
                
            # check for closest detection
            # Todo make distance thres relative to speed and frames without detection.
            distances = bbox_distance(cur_bbox, bboxes_array)
            closest_box = relevant_idx[distances[relevant_idx].argmin()] # min distance from matching objects
            if distances[closest_box] < self.dist_thres:
                self.logger.debug(f"Reinitialize Object id: {obj.id} of type: {obj.class_type} due close distance")
                bbox = tuple(map(int, bboxes_array[greatest_overlap]))
#                 bbox = tuple(np.around(bboxes_array[closest_box]).astype(int)) # conversion for opencv
                obj.reinitialize(frame, bbox)
                detections = np.delete(detections, closest_box, axis=0)
                continue
            if obj.frames_without_detection > self.no_detection_thres:
                to_remove.append(i)

        # remove undetected objects
        self.objects = [self.objects[i] for i in range(len(self.objects)) if i not in to_remove]

        # add new detections
        for det in detections:
            self.add(self.default_tracker, frame, tuple(det[:4]), int(det[5]), xyxy=False)
        self.logger.info(self.get_objects_metadata())
