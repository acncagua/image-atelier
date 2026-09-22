import unittest
from qwen_timing import Timing

class Timings(unittest.TestCase):
    def test_preparation_is_not_charged_to_first_step(self):
        now=[0.0];t=Timing(lambda:now[0])
        now[0]=2;t.enter('loading')
        now[0]=12;t.enter('inference_start')
        now[0]=17;t.first_forward()
        now[0]=18;t.first_forward()  # second CFG forward must not reset the timer
        now[0]=20;t.step(0);t.enter('generating')
        now[0]=24;t.step(1)
        now[0]=24;t.enter('decoding')
        now[0]=30;t.enter('saving')
        now[0]=31;t.enter('completed')
        result=t.snapshot()
        self.assertEqual(result['steps'],[{'step':1,'ms':3000},{'step':2,'ms':4000}])
        self.assertEqual(result['stages_ms']['inference_start'],5000)
        self.assertEqual(result['stages_ms']['denoising'],7000)
        self.assertEqual(sum(result['stages_ms'].values()),result['total_ms'])
