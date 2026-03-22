"""Generate SharanBot logo and architecture diagram as PNG files."""
import struct
import zlib
import math

def create_png(width, height, pixels):
    """Create a PNG file from raw RGBA pixel data."""
    def chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return struct.pack('>I', len(data)) + c + crc

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    
    raw_data = b''
    for y in range(height):
        raw_data += b'\x00'  # filter byte
        for x in range(width):
            idx = (y * width + x) * 4
            raw_data += bytes(pixels[idx:idx+4])
    
    compressed = zlib.compress(raw_data)
    idat = chunk(b'IDAT', compressed)
    iend = chunk(b'IEND', b'')
    
    return header + ihdr + idat + iend

def draw_filled_circle(pixels, width, cx, cy, r, color):
    """Draw a filled circle."""
    for y in range(max(0, cy-r), min(cy+r+1, len(pixels)//(width*4))):
        for x in range(max(0, cx-r), min(cx+r+1, width)):
            dx, dy = x - cx, y - cy
            if dx*dx + dy*dy <= r*r:
                idx = (y * width + x) * 4
                # Alpha blending
                sa = color[3] / 255.0
                da = pixels[idx+3] / 255.0
                oa = sa + da * (1 - sa)
                if oa > 0:
                    pixels[idx] = int((color[0] * sa + pixels[idx] * da * (1 - sa)) / oa)
                    pixels[idx+1] = int((color[1] * sa + pixels[idx+1] * da * (1 - sa)) / oa)
                    pixels[idx+2] = int((color[2] * sa + pixels[idx+2] * da * (1 - sa)) / oa)
                    pixels[idx+3] = int(oa * 255)

def draw_rounded_rect(pixels, width, height, x1, y1, x2, y2, r, color):
    """Draw a filled rounded rectangle."""
    for y in range(max(0, y1), min(y2+1, height)):
        for x in range(max(0, x1), min(x2+1, width)):
            inside = False
            # Check corners
            if x < x1+r and y < y1+r:
                inside = (x-x1-r)**2 + (y-y1-r)**2 <= r*r
            elif x > x2-r and y < y1+r:
                inside = (x-x2+r)**2 + (y-y1-r)**2 <= r*r
            elif x < x1+r and y > y2-r:
                inside = (x-x1-r)**2 + (y-y2+r)**2 <= r*r
            elif x > x2-r and y > y2-r:
                inside = (x-x2+r)**2 + (y-y2+r)**2 <= r*r
            else:
                inside = True
            
            if inside:
                idx = (y * width + x) * 4
                pixels[idx] = color[0]
                pixels[idx+1] = color[1]
                pixels[idx+2] = color[2]
                pixels[idx+3] = color[3]

def draw_gradient_rect(pixels, width, height, x1, y1, x2, y2, color1, color2):
    """Draw a rectangle with horizontal gradient."""
    for y in range(max(0, y1), min(y2+1, height)):
        for x in range(max(0, x1), min(x2+1, width)):
            t = (x - x1) / max(1, x2 - x1)
            r = int(color1[0] * (1-t) + color2[0] * t)
            g = int(color1[1] * (1-t) + color2[1] * t)
            b = int(color1[2] * (1-t) + color2[2] * t)
            idx = (y * width + x) * 4
            pixels[idx] = r
            pixels[idx+1] = g
            pixels[idx+2] = b
            pixels[idx+3] = 255

def draw_thick_line(pixels, width, height, x1, y1, x2, y2, thickness, color):
    """Draw a thick line."""
    dx = x2 - x1
    dy = y2 - y1
    length = max(1, int(math.sqrt(dx*dx + dy*dy)))
    for i in range(length + 1):
        t = i / max(1, length)
        cx = int(x1 + dx * t)
        cy = int(y1 + dy * t)
        draw_filled_circle(pixels, width, cx, cy, thickness // 2, color)

def draw_ring(pixels, width, cx, cy, outer_r, inner_r, color):
    """Draw a ring (circle with hole)."""
    h = len(pixels) // (width * 4)
    for y in range(max(0, cy-outer_r), min(cy+outer_r+1, h)):
        for x in range(max(0, cx-outer_r), min(cx+outer_r+1, width)):
            dx, dy = x - cx, y - cy
            dist_sq = dx*dx + dy*dy
            if inner_r*inner_r <= dist_sq <= outer_r*outer_r:
                idx = (y * width + x) * 4
                pixels[idx] = color[0]
                pixels[idx+1] = color[1]
                pixels[idx+2] = color[2]
                pixels[idx+3] = color[3]

def draw_bolt(pixels, width, height, cx, cy, size, color):
    """Draw a lightning bolt symbol."""
    points = [
        (cx - size//4, cy - size//2),
        (cx + size//6, cy - size//8),
        (cx - size//8, cy - size//8),
        (cx + size//4, cy + size//2),
        (cx - size//12, cy + size//8),
        (cx + size//8, cy + size//8),
    ]
    # Draw as thick lines
    for i in range(0, len(points)-1, 1):
        if i == 2 or i == 4:
            continue
        draw_thick_line(pixels, width, height, 
                       points[i][0], points[i][1],
                       points[i+1][0], points[i+1][1],
                       max(3, size//8), color)
    # Connect the bolt shape
    draw_thick_line(pixels, width, height, points[1][0], points[1][1], points[2][0], points[2][1], max(3, size//8), color)
    draw_thick_line(pixels, width, height, points[2][0], points[2][1], points[3][0], points[3][1], max(3, size//8), color)
    draw_thick_line(pixels, width, height, points[4][0], points[4][1], points[5][0], points[5][1], max(3, size//8), color)

def create_sharanbot_logo():
    """Create the SharanBot logo - a modern robot/AI assistant icon."""
    w, h = 500, 500
    pixels = bytearray([0] * w * h * 4)
    
    # Colors - Modern blue/purple gradient theme
    primary = (41, 98, 255, 255)       # Bright blue
    secondary = (124, 58, 237, 255)    # Purple
    accent = (0, 212, 255, 255)        # Cyan accent
    white = (255, 255, 255, 255)
    dark = (15, 23, 42, 255)           # Dark background for contrast elements
    light_bg = (241, 245, 249, 255)    # Light gray
    
    # === Draw Robot Head (main shape) ===
    # Rounded rectangle for robot head
    draw_rounded_rect(pixels, w, h, 100, 120, 400, 360, 40, primary)
    
    # Gradient overlay on head (lighter at top)
    for y in range(120, 361):
        t = (y - 120) / 240.0
        for x in range(100, 401):
            # Check if inside rounded rect
            inside = True
            r = 40
            if x < 140 and y < 160:
                inside = (x-140)**2 + (y-160)**2 <= r*r
            elif x > 360 and y < 160:
                inside = (x-360)**2 + (y-160)**2 <= r*r
            elif x < 140 and y > 320:
                inside = (x-140)**2 + (y-320)**2 <= r*r
            elif x > 360 and y > 320:
                inside = (x-360)**2 + (y-320)**2 <= r*r
            
            if inside:
                idx = (y * w + x) * 4
                r_val = int(primary[0] * (1-t*0.3) + secondary[0] * t*0.3)
                g_val = int(primary[1] * (1-t*0.3) + secondary[1] * t*0.3)
                b_val = int(primary[2] * (1-t*0.3) + secondary[2] * t*0.3)
                pixels[idx] = r_val
                pixels[idx+1] = g_val
                pixels[idx+2] = b_val
                pixels[idx+3] = 255

    # === Eyes ===
    # Left eye - white circle with dark pupil
    draw_filled_circle(pixels, w, 195, 230, 35, white)
    draw_filled_circle(pixels, w, 200, 225, 18, dark)
    draw_filled_circle(pixels, w, 205, 220, 6, accent)  # Eye glint
    
    # Right eye
    draw_filled_circle(pixels, w, 305, 230, 35, white)
    draw_filled_circle(pixels, w, 310, 225, 18, dark)
    draw_filled_circle(pixels, w, 315, 220, 6, accent)  # Eye glint
    
    # === Mouth - friendly smile ===
    for angle in range(200, 341):
        rad = math.radians(angle)
        for t in range(4):
            x = int(250 + (50 + t) * math.cos(rad))
            y = int(300 + (30 + t) * math.sin(rad))
            if 0 <= x < w and 0 <= y < h:
                idx = (y * w + x) * 4
                pixels[idx] = accent[0]
                pixels[idx+1] = accent[1]
                pixels[idx+2] = accent[2]
                pixels[idx+3] = accent[3]
    
    # === Antenna ===
    draw_thick_line(pixels, w, h, 250, 120, 250, 70, 6, primary)
    draw_filled_circle(pixels, w, 250, 60, 15, accent)
    draw_filled_circle(pixels, w, 250, 60, 8, white)
    
    # === Antenna signal waves ===
    for r_offset in [25, 40]:
        for angle in range(220, 321):
            rad = math.radians(angle)
            for t in range(3):
                x = int(250 + (r_offset + t) * math.cos(rad))
                y = int(60 + (r_offset + t) * math.sin(rad))
                if 0 <= x < w and 0 <= y < h:
                    idx = (y * w + x) * 4
                    pixels[idx] = accent[0]
                    pixels[idx+1] = accent[1]
                    pixels[idx+2] = accent[2]
                    pixels[idx+3] = 180
    
    # === Ears (side panels) ===
    draw_rounded_rect(pixels, w, h, 70, 190, 100, 280, 10, secondary)
    draw_rounded_rect(pixels, w, h, 400, 190, 430, 280, 10, secondary)
    
    # === Body ===
    draw_rounded_rect(pixels, w, h, 150, 370, 350, 440, 20, secondary)
    
    # Lightning bolt on body (power/AI symbol)
    draw_bolt(pixels, w, h, 250, 405, 40, accent)
    
    # === Arms ===
    draw_rounded_rect(pixels, w, h, 120, 375, 150, 430, 10, primary)
    draw_rounded_rect(pixels, w, h, 350, 375, 380, 430, 10, primary)
    
    png_data = create_png(w, h, pixels)
    with open('sharanbot_logo.png', 'wb') as f:
        f.write(png_data)
    print("Created sharanbot_logo.png (500x500)")

def create_sharanbot_arch():
    """Create SharanBot architecture diagram."""
    w, h = 800, 500
    pixels = bytearray([0] * w * h * 4)
    
    # Background
    bg = (248, 250, 252, 255)
    for y in range(h):
        for x in range(w):
            idx = (y * w + x) * 4
            pixels[idx] = bg[0]
            pixels[idx+1] = bg[1]
            pixels[idx+2] = bg[2]
            pixels[idx+3] = bg[3]
    
    primary = (41, 98, 255, 255)
    secondary = (124, 58, 237, 255)
    accent = (0, 212, 255, 255)
    green = (16, 185, 129, 255)
    orange = (245, 158, 11, 255)
    dark = (15, 23, 42, 255)
    white = (255, 255, 255, 255)
    
    # Title bar
    draw_rounded_rect(pixels, w, h, 250, 15, 550, 55, 12, primary)
    
    # Core Agent box (center)
    draw_rounded_rect(pixels, w, h, 280, 120, 520, 220, 15, primary)
    
    # Channels box (left)
    draw_rounded_rect(pixels, w, h, 30, 150, 220, 350, 15, secondary)
    
    # Tools box (right)  
    draw_rounded_rect(pixels, w, h, 580, 150, 770, 350, 15, green)
    
    # Providers box (bottom center)
    draw_rounded_rect(pixels, w, h, 280, 280, 520, 380, 15, orange)
    
    # Memory/Session box (bottom)
    draw_rounded_rect(pixels, w, h, 300, 410, 500, 470, 15, accent)
    
    # Connecting lines
    draw_thick_line(pixels, w, h, 220, 240, 280, 170, 3, dark)
    draw_thick_line(pixels, w, h, 520, 170, 580, 240, 3, dark)
    draw_thick_line(pixels, w, h, 400, 220, 400, 280, 3, dark)
    draw_thick_line(pixels, w, h, 400, 380, 400, 410, 3, dark)
    
    # Small dots at connection points
    for cx, cy in [(220,240), (280,170), (520,170), (580,240), (400,220), (400,280), (400,380), (400,410)]:
        draw_filled_circle(pixels, w, cx, cy, 5, dark)
    
    # Inner labels would need text rendering - we'll use colored indicators instead
    # Core - small robot icon
    draw_filled_circle(pixels, w, 400, 170, 20, white)
    draw_filled_circle(pixels, w, 393, 166, 5, dark)
    draw_filled_circle(pixels, w, 407, 166, 5, dark)
    for angle in range(200, 341):
        rad = math.radians(angle)
        for t in range(2):
            x = int(400 + (10 + t) * math.cos(rad))
            y = int(175 + (6 + t) * math.sin(rad))
            if 0 <= x < w and 0 <= y < h:
                idx = (y * w + x) * 4
                pixels[idx] = primary[0]
                pixels[idx+1] = primary[1]
                pixels[idx+2] = primary[2]
                pixels[idx+3] = 255
    
    # Channel icons (small circles)
    for i, cy in enumerate([190, 230, 270, 310]):
        draw_filled_circle(pixels, w, 60, cy, 12, white)
        draw_filled_circle(pixels, w, 60, cy, 6, secondary)
    
    # Tool icons
    for i, cy in enumerate([190, 230, 270, 310]):
        draw_filled_circle(pixels, w, 740, cy, 12, white)
        draw_filled_circle(pixels, w, 740, cy, 6, green)
        
    # Provider icons
    for i, cx in enumerate([320, 370, 420, 470]):
        draw_filled_circle(pixels, w, cx, 330, 12, white)
        draw_filled_circle(pixels, w, cx, 330, 6, orange)

    png_data = create_png(w, h, pixels)
    with open('sharanbot_arch.png', 'wb') as f:
        f.write(png_data)
    print("Created sharanbot_arch.png (800x500)")

if __name__ == '__main__':
    create_sharanbot_logo()
    create_sharanbot_arch()
    print("Done! Logo and architecture images created.")
