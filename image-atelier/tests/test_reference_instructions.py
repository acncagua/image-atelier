import unittest
from core import prompt_for,canonical_input,reference_instructions


class References(unittest.TestCase):
    def params(self,**extra):
        return {'model':'Qwen-Image-2.1','mode':'generate','change':'庭園でお茶を飲む',
                'reference_sheets':True,'refs':[{'id':'a','role':'body'},
                {'id':'b','role':'reference'},{'id':'c','role':'face'},
                {'id':'d','role':'reference'}],**extra}

    def test_multiple_sheets_follow_actual_order(self):
        text=prompt_for(self.params())
        self.assertTrue(text.startswith('庭園でお茶を飲む'))
        self.assertIn('<image2>',text);self.assertIn('<image4>',text)
        self.assertNotIn('<image1>',text)
        self.assertEqual(text.count('この画像内の各人物像'),2)

    def test_mask_is_last_and_does_not_shift_references(self):
        for mode in ('polish','inpaint'):
            text=prompt_for(self.params(mode=mode,target='base'))
            self.assertIn('<image3>',text);self.assertIn('<image5>',text)
            self.assertNotIn('<image6>',text)

    def test_off_or_no_sheet_has_no_extra_text(self):
        self.assertEqual(prompt_for(self.params(reference_sheets=False)),'庭園でお茶を飲む')
        self.assertEqual(prompt_for(self.params(refs=[])),'庭園でお茶を飲む')

    def test_reordering_recalculates_and_setting_is_durable(self):
        p=self.params();p['refs']=list(reversed(p['refs']))
        text=prompt_for(p)
        self.assertIn('<image1>',text);self.assertIn('<image3>',text)
        self.assertTrue(canonical_input(p)['reference_sheets'])
        self.assertNotIn('reference_sheets',canonical_input({**p,'reference_sheets':False}))

    def test_openai_receives_same_opt_in_instructions(self):
        p=self.params(model='gpt-image-2.5-sunburst',mode='inpaint',target='base')
        text=prompt_for(p)
        self.assertIn('画像3: 人物のリファレンス資料',text)
        self.assertTrue(text.endswith(reference_instructions(p)[-1]))
