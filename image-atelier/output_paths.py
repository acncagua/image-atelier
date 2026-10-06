"""Independent export destinations; keep legacy single-folder configurations usable."""
FIELDS={'t2i':'output_t2i','i2i':'output_i2i','upscale':'output_upscale'}
LABELS={'t2i':'T2I','i2i':'I2I／Inpaint','upscale':'アップスケール'}

def category_for_mode(mode):
    if mode=='generate':return 't2i'
    if mode in ('polish','inpaint'):return 'i2i'
    if mode=='upscale':return 'upscale'
    raise ValueError('保存先の区分が不正です。')
