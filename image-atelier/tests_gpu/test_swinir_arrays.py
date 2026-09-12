"""Run in .venv-swinir. Array/mock predictor tests; actual GPU results are separate."""
import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:import numpy as np
except ImportError:raise unittest.SkipTest('Run this file in .venv-swinir')
from PIL import Image
from swinir_runner import tiled_rgb,prepare_rgb,Cancelled

class Arrays(unittest.TestCase):
    def test_rgb_order_padding_and_overlap(self):
        rng=np.random.default_rng(3);rgb=rng.integers(0,256,(35,47,3),dtype=np.uint8);shapes=[]
        def predict(p):shapes.append(p.shape);return np.repeat(np.repeat(p,4,axis=0),4,axis=1)
        output=tiled_rgb(rgb.astype(np.float32)/255,predict,8,32,8)
        np.testing.assert_array_equal(output,np.repeat(np.repeat(rgb,4,axis=0),4,axis=1))
        self.assertTrue(all(h%8==w%8==0 for h,w,c in shapes))
    def test_hidden_rgb_filled_without_alpha_loss(self):
        image=Image.new('RGBA',(9,9),(0,255,0,0));image.putpixel((4,4),(255,0,0,255))
        rgb,alpha=prepare_rgb(image);self.assertTrue(np.all(rgb[:,:,0]==1));self.assertTrue(np.all(rgb[:,:,1]==0));self.assertEqual(np.count_nonzero(alpha),1)
    def test_tile_cancel(self):
        with self.assertRaises(Cancelled):tiled_rgb(np.zeros((32,32,3)),lambda x:x,8,32,8,cancel=lambda:True)
    def test_tiny_odd_input_is_padded_then_cropped(self):
        rgb=np.full((5,9,3),.5,dtype=np.float32);seen=[]
        def predict(p):seen.append(p.shape);return np.repeat(np.repeat(p,4,0),4,1)
        output=tiled_rgb(rgb,predict,8,32,8)
        self.assertEqual(seen,[(8,16,3)]);self.assertEqual(output.shape,(20,36,3))

if __name__=='__main__':unittest.main()
