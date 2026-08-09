import cv2
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse
import threading
import time

app = FastAPI()

face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')

# Global camera object
camera = cv2.VideoCapture(0)

def generate_frames():
    while True:
        success, frame = camera.read()
        if not success:
            # If camera fails, sleep a bit and try again, or yield a blank image
            time.sleep(0.1)
            continue
        
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Распознавание лица
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
        
        output_frame = frame
        
        for (x, y, w, h) in faces:
            roi_gray = gray[y:y+h, x:x+w]
            roi_color = frame[y:y+h, x:x+w]
            
            # Распознавание глаз
            eyes = eye_cascade.detectMultiScale(roi_gray, 1.1, 4)
            if len(eyes) > 0:
                ex, ey, ew, eh = eyes[0]
                
                margin_x = int(ew * 0.3)
                margin_y = int(eh * 0.3)
                
                eye_y1 = max(0, ey - margin_y)
                eye_y2 = min(roi_color.shape[0], ey + eh + margin_y)
                eye_x1 = max(0, ex - margin_x)
                eye_x2 = min(roi_color.shape[1], ex + ew + margin_x)
                
                eye_crop = roi_color[eye_y1:eye_y2, eye_x1:eye_x2]
                
                if eye_crop.size > 0:
                    # Увеличение изображения глаза до нормальных размеров
                    output_frame = cv2.resize(eye_crop, (640, 480), interpolation=cv2.INTER_LINEAR)
                    
                    # Пишем текст для красоты
                    cv2.putText(output_frame, "Eye Tracking Mode (Zoomed)", (20, 40), 
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                break
            
        ret, buffer = cv2.imencode('.jpg', output_frame)
        frame_bytes = buffer.tobytes()
        
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.get("/video_feed")
def video_feed():
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/")
def index():
    html_content = """
    <html>
        <head>
            <title>Eye Tracking & Zoom</title>
            <style>
                body { background-color: #222; color: white; font-family: sans-serif; text-align: center; }
                img { border: 2px solid #fff; border-radius: 8px; margin-top: 20px; max-width: 100%; }
            </style>
        </head>
        <body>
            <h1>Трансляция: Фокус на глаз</h1>
            <p>Камера распознает лицо, затем глаз, приближает (зум) и передает видео сюда.</p>
            <img src="/video_feed" alt="Video stream" />
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == '__main__':
    # Сервер доступен по IP-адресу на порту 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
