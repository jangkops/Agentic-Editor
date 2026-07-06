from PIL import Image, ImageDraw, ImageFont
import os

def create_architecture_diagram():
    # Create image
    width, height = 1024, 768
    img = Image.new('RGB', (width, height), color='#f8f9fa')
    draw = ImageDraw.Draw(img)
    
    # Colors
    colors = {
        'frontend': '#3498db',
        'frontend_border': '#2980b9',
        'backend': '#2ecc71',
        'backend_border': '#27ae60', 
        'cloud': '#e67e22',
        'cloud_border': '#d35400',
        'text': '#2c3e50',
        'text_light': '#7f8c8d',
        'white': '#ffffff',
        'feature_bg': '#ecf0f1',
        'feature_border': '#bdc3c7'
    }
    
    # Try to load fonts, fall back to default if not available
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 24)
        heading_font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 18)
        text_font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 14)
        small_font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 12)
    except:
        title_font = ImageFont.load_default()
        heading_font = ImageFont.load_default() 
        text_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
    
    def draw_rounded_rect(xy, fill, outline, width=2):
        x1, y1, x2, y2 = xy
        draw.rectangle(xy, fill=fill, outline=outline, width=width)
    
    def draw_text_centered(text, xy, font, fill):
        x, y = xy
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        draw.text((x - text_width//2, y), text, font=font, fill=fill)
    
    def draw_arrow(start, end):
        x1, y1 = start
        x2, y2 = end
        draw.line([start, end], fill=colors['text'], width=2)
        
        # Arrow head
        import math
        angle = math.atan2(y2 - y1, x2 - x1)
        head_length = 10
        head_angle = math.pi / 6
        
        head1_x = x2 - head_length * math.cos(angle - head_angle)
        head1_y = y2 - head_length * math.sin(angle - head_angle)
        head2_x = x2 - head_length * math.cos(angle + head_angle)
        head2_y = y2 - head_length * math.sin(angle + head_angle)
        
        draw.line([(x2, y2), (head1_x, head1_y)], fill=colors['text'], width=2)
        draw.line([(x2, y2), (head2_x, head2_y)], fill=colors['text'], width=2)
    
    # Title
    draw_text_centered('Agentic Editor - System Architecture', (512, 40), title_font, colors['text'])
    
    # Frontend Layer
    draw.text((50, 100), 'Frontend Layer - Electron Desktop App', font=heading_font, fill=colors['text'])
    
    # File Explorer
    draw_rounded_rect((80, 120, 300, 240), colors['frontend'], colors['frontend_border'])
    draw_text_centered('File Explorer', (190, 135), text_font, colors['white'])
    draw.text((90, 155), '• Directory Tree', font=small_font, fill=colors['white'])
    draw.text((90, 175), '• Git Integration', font=small_font, fill=colors['white'])
    draw.text((90, 195), '• File Operations', font=small_font, fill=colors['white'])
    draw.text((90, 215), '• Branch Management', font=small_font, fill=colors['white'])
    
    # Monaco Editor
    draw_rounded_rect((320, 120, 540, 240), colors['frontend'], colors['frontend_border'])
    draw_text_centered('Monaco Editor', (430, 135), text_font, colors['white'])
    draw.text((330, 155), '• Code Editing', font=small_font, fill=colors['white'])
    draw.text((330, 175), '• Syntax Highlighting', font=small_font, fill=colors['white'])
    draw.text((330, 195), '• Auto-completion', font=small_font, fill=colors['white'])
    draw.text((330, 215), '• Search & Replace', font=small_font, fill=colors['white'])
    
    # AI Chat Panel
    draw_rounded_rect((560, 120, 780, 240), colors['frontend'], colors['frontend_border'])
    draw_text_centered('AI Chat Panel', (670, 135), text_font, colors['white'])
    draw.text((570, 155), '• Single Model Chat', font=small_font, fill=colors['white'])
    draw.text((570, 175), '• Parallel Inference', font=small_font, fill=colors['white'])
    draw.text((570, 195), '• Consensus Engine', font=small_font, fill=colors['white'])
    draw.text((570, 215), '• Agent Workflow', font=small_font, fill=colors['white'])
    
    # Backend Layer
    draw.text((50, 320), 'Backend Layer - FastAPI Python Server', font=heading_font, fill=colors['text'])
    
    # Gateway Client
    draw_rounded_rect((80, 340, 280, 440), colors['backend'], colors['backend_border'])
    draw_text_centered('Gateway Client', (180, 355), text_font, colors['white'])
    draw.text((90, 375), '• AWS SigV4 Auth', font=small_font, fill=colors['white'])
    draw.text((90, 395), '• HTTP/SSE Streaming', font=small_font, fill=colors['white'])
    draw.text((90, 415), '• Rate Limiting', font=small_font, fill=colors['white'])
    
    # RAG Engine
    draw_rounded_rect((300, 340, 500, 440), colors['backend'], colors['backend_border'])
    draw_text_centered('RAG Engine', (400, 355), text_font, colors['white'])
    draw.text((310, 375), '• TF-IDF Vectorization', font=small_font, fill=colors['white'])
    draw.text((310, 395), '• BM25 Search', font=small_font, fill=colors['white'])
    draw.text((310, 415), '• Hybrid Ranking', font=small_font, fill=colors['white'])
    
    # Agent Tools
    draw_rounded_rect((520, 340, 720, 440), colors['backend'], colors['backend_border'])
    draw_text_centered('Agent Tools', (620, 355), text_font, colors['white'])
    draw.text((530, 375), '• File Operations', font=small_font, fill=colors['white'])
    draw.text((530, 395), '• Command Execution', font=small_font, fill=colors['white'])
    draw.text((530, 415), '• Project Search', font=small_font, fill=colors['white'])
    
    # Conversation Memory
    draw_rounded_rect((740, 340, 940, 440), colors['backend'], colors['backend_border'])
    draw_text_centered('Memory System', (840, 355), text_font, colors['white'])
    draw.text((750, 375), '• Conversation History', font=small_font, fill=colors['white'])
    draw.text((750, 395), '• Summary Checkpoints', font=small_font, fill=colors['white'])
    draw.text((750, 415), '• Context Management', font=small_font, fill=colors['white'])
    
    # Cloud Layer
    draw.text((50, 520), 'Cloud Layer - AWS Bedrock Gateway', font=heading_font, fill=colors['text'])
    
    # Bedrock Gateway
    draw_rounded_rect((200, 540, 480, 660), colors['cloud'], colors['cloud_border'])
    draw_text_centered('AWS Bedrock Gateway', (340, 555), text_font, colors['white'])
    draw.text((210, 575), '• API Gateway + Lambda Functions', font=small_font, fill=colors['white'])
    draw.text((210, 595), '• Authentication & Authorization', font=small_font, fill=colors['white'])
    draw.text((210, 615), '• Usage Tracking & Quotas', font=small_font, fill=colors['white'])
    draw.text((210, 635), '• Cost Management', font=small_font, fill=colors['white'])
    
    # LLM Models
    draw_rounded_rect((500, 540, 780, 660), colors['cloud'], colors['cloud_border'])
    draw_text_centered('LLM Model Runtime', (640, 555), text_font, colors['white'])
    draw.text((510, 575), '• Claude (Anthropic)', font=small_font, fill=colors['white'])
    draw.text((510, 595), '• GPT (OpenAI)', font=small_font, fill=colors['white'])
    draw.text((510, 615), '• Llama (Meta)', font=small_font, fill=colors['white'])
    draw.text((510, 635), '• 70+ Models Available', font=small_font, fill=colors['white'])
    
    # Key Features Box
    draw_rounded_rect((800, 120, 980, 320), colors['feature_bg'], colors['feature_border'])
    draw_text_centered('Key Features', (890, 135), text_font, colors['text'])
    draw.text((810, 155), '• Multi-model Inference', font=small_font, fill=colors['text'])
    draw.text((810, 175), '• Parallel Processing', font=small_font, fill=colors['text'])
    draw.text((810, 195), '• Consensus Generation', font=small_font, fill=colors['text'])
    draw.text((810, 215), '• Agent Automation', font=small_font, fill=colors['text'])
    draw.text((810, 235), '• Project-aware RAG', font=small_font, fill=colors['text'])
    draw.text((810, 255), '• Real-time Streaming', font=small_font, fill=colors['text'])
    draw.text((810, 275), '• Git Integration', font=small_font, fill=colors['text'])
    draw.text((810, 295), '• AWS SSO Auth', font=small_font, fill=colors['text'])
    
    # Connections (arrows)
    # Frontend to Backend
    draw_arrow((430, 240), (430, 340))
    draw.text((440, 280), 'HTTP/SSE', font=small_font, fill=colors['text_light'])
    draw.text((440, 295), 'localhost:8765', font=small_font, fill=colors['text_light'])
    
    # Backend to Cloud
    draw_arrow((430, 440), (430, 540))
    draw.text((440, 480), 'HTTPS/SigV4', font=small_font, fill=colors['text_light'])
    draw.text((440, 495), 'AWS API Gateway', font=small_font, fill=colors['text_light'])
    
    # Gateway to Models
    draw_arrow((480, 600), (500, 600))
    draw.text((485, 585), 'Invoke', font=small_font, fill=colors['text_light'])
    
    return img

# Generate the diagram
if __name__ == '__main__':
    img = create_architecture_diagram()
    img.save('.generated/agentic-editor-architecture.png', 'PNG')
    print("Architecture diagram saved to .generated/agentic-editor-architecture.png")