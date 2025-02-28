import os
import torch
import whisper
from datetime import timedelta
from flask import Flask, request, render_template_string, send_file, redirect, url_for
from werkzeug.utils import secure_filename
import uuid

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['RESULTS_FOLDER'] = 'results'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # حد أقصى 500 ميجابايت

# التأكد من وجود مجلدات الرفع والنتائج
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['RESULTS_FOLDER'], exist_ok=True)

# قالب HTML
HTML_TEMPLATE = """
<!DOCTYPE html>
<html dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>استخراج النص من الفيديو</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            background-color: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
        }
        form {
            margin-top: 20px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        select, input[type="file"] {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 12px 20px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
            width: 100%;
        }
        button:hover {
            background-color: #45a049;
        }
        .error {
            color: red;
            margin-top: 10px;
            padding: 10px;
            background-color: #ffebee;
            border-radius: 5px;
        }
        .loading {
            display: none;
            text-align: center;
            margin-top: 20px;
        }
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #3498db;
            border-radius: 50%;
            width: 30px;
            height: 30px;
            animation: spin 2s linear infinite;
            margin: 0 auto;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>استخراج النص من الفيديو بصيغة SRT</h1>
        
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        
        <form method="post" enctype="multipart/form-data" onsubmit="showLoading()">
            <div class="form-group">
                <label for="video">اختر ملف الفيديو:</label>
                <input type="file" id="video" name="video" accept="video/*" required>
            </div>
            
            <div class="form-group">
                <label for="model_size">حجم النموذج:</label>
                <select id="model_size" name="model_size">
                    <option value="tiny">صغير جدًا (أسرع)</option>
                    <option value="base" selected>أساسي</option>
                    <option value="small">صغير</option>
                    <option value="medium">متوسط</option>
                    <option value="large">كبير (أكثر دقة)</option>
                </select>
            </div>
            
            <div class="form-group">
                <label for="language">اللغة:</label>
                <select id="language" name="language">
                    <option value="auto" selected>تلقائي</option>
                    <option value="ar">العربية</option>
                    <option value="en">الإنجليزية</option>
                    <option value="fr">الفرنسية</option>
                    <option value="de">الألمانية</option>
                    <option value="es">الإسبانية</option>
                    <option value="it">الإيطالية</option>
                    <option value="ja">اليابانية</option>
                    <option value="ko">الكورية</option>
                    <option value="pt">البرتغالية</option>
                    <option value="ru">الروسية</option>
                    <option value="tr">التركية</option>
                    <option value="zh">الصينية</option>
                </select>
            </div>
            
            <button type="submit">معالجة الفيديو</button>
        </form>
        
        <div id="loading" class="loading">
            <p>جاري معالجة الفيديو...</p>
            <div class="spinner"></div>
            <p>قد تستغرق المعالجة بعض الوقت اعتمادًا على حجم الفيديو ونوع النموذج</p>
        </div>
    </div>
    
    <script>
        function showLoading() {
            document.getElementById('loading').style.display = 'block';
        }
    </script>
</body>
</html>
"""

def format_time(seconds):
    """تحويل الوقت بالثواني إلى تنسيق SRT (ساعات:دقائق:ثواني,مللي ثانية)"""
    td = timedelta(seconds=seconds)
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = td.microseconds // 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def create_srt(segments, output_file):
    """إنشاء ملف SRT من قطع النص"""
    with open(output_file, 'w', encoding='utf-8') as f:
        for i, segment in enumerate(segments, start=1):
            start_time = format_time(segment['start'])
            end_time = format_time(segment['end'])
            text = segment['text'].strip()
            
            f.write(f"{i}\n")
            f.write(f"{start_time} --> {end_time}\n")
            f.write(f"{text}\n\n")

def extract_audio_from_video(video_path, audio_path):
    """استخراج الصوت من الفيديو باستخدام ffmpeg"""
    os.system(f"ffmpeg -i \"{video_path}\" -q:a 0 -map a \"{audio_path}\" -y")
    return audio_path

def process_video(video_path, model_size, language):
    """معالجة الفيديو واستخراج النص باستخدام Whisper"""
    # إنشاء اسم فريد للملفات المؤقتة والنتائج
    unique_id = str(uuid.uuid4())
    video_name = os.path.splitext(os.path.basename(video_path))[0]
    
    # تحديد مسارات الملفات
    audio_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{unique_id}.wav")
    srt_path = os.path.join(app.config['RESULTS_FOLDER'], f"{video_name}_{unique_id}.srt")
    
    # استخراج الصوت
    extract_audio_from_video(video_path, audio_path)
    
    # تحميل نموذج Whisper
    model = whisper.load_model(model_size)
    
    # إجراء التعرف على الكلام
    transcription_options = {
        "task": "transcribe",
        "verbose": False
    }
    
    if language and language != "auto":
        transcription_options["language"] = language
    
    result = model.transcribe(audio_path, **transcription_options)
    
    # إنشاء ملف SRT
    create_srt(result["segments"], srt_path)
    
    # تنظيف الملفات المؤقتة
    os.remove(audio_path)
    
    return srt_path

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        # التحقق من وجود ملف في الطلب
        if 'video' not in request.files:
            return render_template_string(HTML_TEMPLATE, error="لم يتم اختيار ملف")
        
        file = request.files['video']
        
        # التحقق من اختيار ملف
        if file.filename == '':
            return render_template_string(HTML_TEMPLATE, error="لم يتم اختيار ملف")
        
        # الحصول على الخيارات الأخرى
        model_size = request.form.get('model_size', 'base')
        language = request.form.get('language', 'auto')
        
        # حفظ الملف المرفوع
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        try:
            # معالجة الفيديو
            srt_path = process_video(file_path, model_size, language)
            
            # حذف ملف الفيديو الأصلي بعد المعالجة
            os.remove(file_path)
            
            # تحويل المستخدم لتحميل ملف SRT
            return redirect(url_for('download_file', filename=os.path.basename(srt_path)))
        
        except Exception as e:
            # في حالة حدوث خطأ أثناء المعالجة
            if os.path.exists(file_path):
                os.remove(file_path)
            return render_template_string(HTML_TEMPLATE, error=f"حدث خطأ أثناء المعالجة: {str(e)}")
    
    return render_template_string(HTML_TEMPLATE, error=None)

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(app.config['RESULTS_FOLDER'], filename), 
                    as_attachment=True, 
                    download_name=filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)