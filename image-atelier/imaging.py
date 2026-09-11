"""Immutable source files; EXIF-oriented, sRGB working PNGs; pixel-space masks."""
import io
import math
from PIL import Image, ImageOps, ImageCms, ImageDraw, ImageFilter, ImageChops

Image.MAX_IMAGE_PIXELS = 40_000_000
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()

def png(image):
    out = io.BytesIO()
    image.save(out, format='PNG', icc_profile=SRGB)
    return out.getvalue()

def normalize(raw):
    with Image.open(io.BytesIO(raw)) as source:
        if source.format not in ('PNG', 'JPEG', 'WEBP'):
            raise ValueError('PNG / JPEG / WebP のみ読み込めます。')
        if source.width * source.height > 40_000_000:
            raise ValueError('入力は4,000万画素以下にしてください。')
        source.load()
        image = ImageOps.exif_transpose(source)
        profile = source.info.get('icc_profile')
        alpha = image.convert('RGBA').getchannel('A')
        if profile:
            try:
                image = ImageCms.profileToProfile(image.convert('RGB'), ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile('sRGB'), outputMode='RGB')
            except Exception as e:
                raise ValueError('色プロファイルを変換できません。sRGB PNGを指定してください。') from e
        else:
            image = image.convert('RGB')
        image = image.convert('RGBA')
        image.putalpha(alpha)
        return image

def mask_image(size, strokes):
    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    if len(strokes) > 2000:
        raise ValueError('マスクのストロークが多すぎます。')
    for stroke in strokes:
        radius = max(1, min(float(stroke['width']), 1000)) / 2
        points = [(float(x), float(y)) for x, y in stroke['points']]
        if not math.isfinite(float(stroke['width'])) or any(not math.isfinite(v) for point in points for v in point):
            raise ValueError('マスクの座標が不正です。')
        if len(points) > 10000:
            raise ValueError('マスクの点が多すぎます。')
        color = 0 if stroke.get('erase') else 255
        if len(points) > 1:
            draw.line(points, fill=color, width=round(radius * 2))
        for x, y in points:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
    return mask

def api_mask(mask):
    image = Image.new('RGBA', mask.size, (255,255,255,255))
    image.putalpha(ImageOps.invert(mask))
    return png(image)

def composite(original, result, mask, feather):
    if original.size != result.size or original.size != mask.size:
        raise ValueError('元画像・結果・マスクの寸法が一致しないため合成できません。')
    # Feather inward: even a blurred selection never affects wholly unpainted pixels.
    alpha = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(feather))) if feather else mask
    return Image.composite(result.convert('RGBA'), original.convert('RGBA'), alpha)
