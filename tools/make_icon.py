"""生成 ggit 的图标：深色圆角底 + 霓虹 git 分支图案。

用法：python tools/make_icon.py
输出：ggit/assets/ggit.ico（多尺寸）、ggit/assets/ggit.png（256px）、ggit/icon_data.py（内嵌的 PNG，程序运行时用）
依赖：Pillow、numpy（只有重新生成图标时才需要，运行 ggit 本身不需要）
"""
import base64
import io
import math
import os

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

CYAN = (56, 232, 255)
VIOLET = (190, 112, 255)
GREEN = (96, 255, 168)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def bezier(p0, p1, p2, p3, n=80):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def background(S, radius):
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32) / (S - 1)
    t = np.clip(0.65 * yy + 0.35 * xx, 0, 1)
    top, bot = np.array([8, 13, 36], np.float32), np.array([34, 14, 74], np.float32)
    rgb = top[None, None, :] * (1 - t[..., None]) + bot[None, None, :] * t[..., None]
    # 中心偏左上的青色光晕
    d = np.sqrt((xx - 0.40) ** 2 + (yy - 0.45) ** 2)
    glow = np.clip(1 - d / 0.75, 0, 1) ** 2.2
    rgb = rgb + glow[..., None] * np.array([6, 70, 96], np.float32)[None, None, :]
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius, fill=255)
    return img, mask


def draw_icon(S=1024, detail=True, k=1.0):
    """k：线条粗细倍数（小尺寸用更粗的线条保证看得清）。"""
    radius = int(S * 0.225)
    img, mask = background(S, radius)
    sc = S / 1024.0
    P = lambda x, y: (x * sc, y * sc)

    # ---- 电路纹理（只在大尺寸画）----
    if detail:
        tr = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(tr)
        col = (*CYAN, 46)
        w = max(2, int(7 * sc))
        for pts in ([P(130, 880), P(130, 760), P(210, 680)],
                    [P(880, 150), P(880, 270), P(800, 350)],
                    [P(210, 150), P(300, 150), P(350, 100)],
                    [P(880, 760), P(880, 880), P(780, 880)]):
            d.line(pts, fill=col, width=w, joint="curve")
            x, y = pts[0]
            r = 15 * sc
            d.ellipse((x - r, y - r, x + r, y + r), outline=(*CYAN, 90), width=max(2, int(6 * sc)))
        img = Image.alpha_composite(img, tr)

    # ---- 霓虹 git 分支图案 ----
    glyph = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glyph)
    lw = int(48 * k * sc)
    x0, y_top, y_bot = 350, 262, 762
    branch_node = (668, 392)
    # 用“沿路径盖圆章”的方式画粗线：边缘平滑、两头是圆角（逐段画线会出现毛边）
    def stroke(points, colors, width):
        r = width / 2.0
        for (x, y), c in zip(points, colors):
            g.ellipse((x - r, y - r, x + r, y + r), fill=(*c, 255))

    # 主干：上青下绿渐变
    N = 500
    main = [P(x0, y_top + (y_bot - y_top) * i / N) for i in range(N + 1)]
    stroke(main, [lerp(CYAN, GREEN, i / N) for i in range(N + 1)], lw)
    # 分支：从主干弯向右上，颜色由绿渐变到紫
    curve = [P(*p) for p in bezier((x0, 650), (x0, 500), (branch_node[0], 590), branch_node, n=600)]
    stroke(curve, [lerp(GREEN, VIOLET, (i / len(curve)) ** 0.8) for i in range(len(curve))], lw)
    # 圆形端点 + 节点（外环 + 暗色内孔 + 亮色圆心）
    hole = (10, 15, 42, 255)
    for (cx, cy), c in (((x0, y_top), CYAN), ((x0, y_bot), GREEN), (branch_node, VIOLET)):
        R = 92 * (0.92 + 0.08 * k) * sc
        r = R - 40 * k * sc
        x, y = P(cx, cy)
        g.ellipse((x - R, y - R, x + R, y + R), fill=(*c, 255))
        g.ellipse((x - r, y - r, x + r, y + r), fill=hole)
        rc = max(2.0, r * 0.42)
        g.ellipse((x - rc, y - rc, x + rc, y + rc), fill=(*lerp(c, (255, 255, 255), 0.55), 255))
    # 发光：两层模糊叠在清晰图案下面
    halo1 = glyph.filter(ImageFilter.GaussianBlur(S * 0.018))
    halo2 = glyph.filter(ImageFilter.GaussianBlur(S * 0.05))
    a2 = halo2.getchannel("A").point(lambda v: min(255, int(v * 1.1)))
    halo2.putalpha(a2)
    img = Image.alpha_composite(img, halo2)
    img = Image.alpha_composite(img, halo1)
    img = Image.alpha_composite(img, glyph)

    # ---- 外框：青 → 紫 渐变细边 ----
    if detail:
        bw = max(3, int(S * 0.012))
        inner = Image.new("L", (S, S), 0)
        ImageDraw.Draw(inner).rounded_rectangle((bw, bw, S - 1 - bw, S - 1 - bw), max(1, radius - bw), fill=255)
        ring = ImageChops.subtract(mask, inner)
        yy, xx = np.mgrid[0:S, 0:S].astype(np.float32) / (S - 1)
        t = np.clip((xx + yy) / 2, 0, 1)[..., None]
        c1, c2 = np.array(CYAN, np.float32), np.array(VIOLET, np.float32)
        border = Image.fromarray((c1 * (1 - t) + c2 * t).astype(np.uint8), "RGB").convert("RGBA")
        border.putalpha(ring.point(lambda v: int(v * 0.75)))
        img = Image.alpha_composite(img, border)

    img.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    return img


def render(size):
    if size >= 96:
        return draw_icon(1024, True, 1.0).resize((size, size), Image.LANCZOS)
    big = draw_icon(512, False, 1.45)          # 小尺寸：去掉细节、线条加粗
    return big.resize((size, size), Image.LANCZOS)


def main():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    assets = os.path.join(root, "ggit", "assets")
    os.makedirs(assets, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = {s: render(s) for s in sizes}
    imgs[256].save(os.path.join(assets, "ggit.png"))
    imgs[256].save(os.path.join(assets, "ggit.ico"), format="ICO",
                   sizes=[(s, s) for s in sizes], append_images=[imgs[s] for s in sizes if s != 256])
    # 内嵌到代码里：避免打包成 exe 后找不到图片文件
    out = ["# 由 tools/make_icon.py 自动生成，请不要手改。", '"""内嵌的程序图标（PNG，base64）。"""', "", "ICON_PNGS = {"]
    for s in (256, 64, 32, 16):
        buf = io.BytesIO()
        imgs[s].save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        out.append(f"    {s}: (")
        for i in range(0, len(b64), 100):
            out.append(f'        "{b64[i:i + 100]}"')
        out.append("    ),")
    out.append("}")
    with open(os.path.join(root, "ggit", "icon_data.py"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    # 预览图（不进包）：各尺寸并排
    sheet = Image.new("RGBA", (256 + 128 + 64 + 48 + 32 + 24 + 16 + 7 * 16, 256 + 16), (30, 30, 30, 255))
    x = 8
    for s in (256, 128, 64, 48, 32, 24, 16):
        sheet.alpha_composite(imgs[s], (x, 8))
        x += s + 16
    sheet.save("/tmp/ggit_icon_preview.png")
    print("ok", os.path.getsize(os.path.join(assets, "ggit.ico")), "bytes ico")


if __name__ == "__main__":
    main()
