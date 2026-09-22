"""Synchronized wall-clock timings, including CPU work and device transfers."""
import time


class Timing:
    def __init__(self, clock=time.perf_counter):
        self.clock=clock
        self.started=self.mark=clock()
        self.stage='initializing'
        self.stages={}
        self.steps=[]
        self.step_mark=None

    def enter(self, stage):
        if stage=='generating' or stage==self.stage:return
        now=self.clock()
        self.stages[self.stage]=self.stages.get(self.stage,0)+(now-self.mark)*1000
        self.stage=stage;self.mark=now

    def first_forward(self):
        if self.step_mark is None:
            self.enter('denoising')
            self.step_mark=self.clock()

    def step(self, index):
        now=self.clock()
        if self.step_mark is not None:
            self.steps.append({'step':index+1,'ms':round((now-self.step_mark)*1000,2)})
        self.step_mark=now

    def snapshot(self):
        now=self.clock();stages=dict(self.stages)
        if self.stage not in ('completed','failed'):
            stages[self.stage]=stages.get(self.stage,0)+(now-self.mark)*1000
        return {'total_ms':round((now-self.started)*1000,2),
                'stages_ms':{k:round(v,2) for k,v in stages.items()},'steps':list(self.steps)}
