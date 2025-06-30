# 后端 Flask 应用 app.py
import base64
import io
from flask import Flask, request, jsonify, send_file
import subprocess
import json
import os
import re
import logging
from io import BytesIO
from PIL import Image
from prompt_templates import SHOPPING_QUESTION_PROMPT
import requests 
import uuid
from prompt_dispatcher import get_task_prompt
app = Flask(__name__)
CLOUD_DEVICE_IP_PORT = '4af63a6b'
ADB_PATH = r"/Users/shiyibo/WorkSpace/platform-tools/adb"
app_name = 'com.sankuai.meituan.takeoutnew' # 执行的应用的包名,用于开启和关闭应用
LOG_PATH ='./app.log'
BASE_ANNO_PATH='./annotations'
BASE_SCREENSHOT_PATH='./screenshots_tmp'
IMGS_PATH='./imgs_all'
# vLLM 服务地址
API_URL = "http://localhost:8100/v1/chat/completions"
# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),            # 控制台输出
        logging.FileHandler(LOG_PATH, encoding='utf-8')  # 文件输出
    ]
)
# ADB操作
class ADBController:
    @staticmethod
    def connect_device():
        """通过 adb connect 连接远程设备"""
        logging.info(f"尝试连接设备: {CLOUD_DEVICE_IP_PORT}")
        result = subprocess.run([ADB_PATH, "connect", CLOUD_DEVICE_IP_PORT], capture_output=True, text=True)
        logging.info(result.stdout)
        if "connected" in result.stdout.lower():
            logging.info(f"成功连接设备：{CLOUD_DEVICE_IP_PORT}")
            return True
        else:
            logging.warning(f"连接失败：{result.stderr}")
            return False

    @staticmethod
    def get_screenshot():
        # 定义目标路径
        local_path = f'{BASE_SCREENSHOT_PATH}/screen_{uuid.uuid4()}.png'
        # 确保本地文件夹存在
        import os
        if not os.path.exists(BASE_SCREENSHOT_PATH):
            os.makedirs(BASE_SCREENSHOT_PATH)

        # 使用adb截屏到设备
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', 'screencap', '-p', '/sdcard/screen.png'])
        # 拉取到本地
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'pull', '/sdcard/screen.png', local_path])
        logging.info(f"截图:{local_path}")
        # 返回图片路径
        return local_path

    @staticmethod
    def execute_command(command):
        # 执行ADB命令
        result = subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', command], capture_output=True)
        return result.stdout.decode()

    @staticmethod
    def restart_app():
        """重启指定应用：先关闭再启动"""
        global app_name
        if not app_name:
            logging.warning("未设置应用包名(app_name)")
            return
        logging.info(f"重启应用：{app_name}")
        # 先关闭应用
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', 'am', 'force-stop', app_name])
        # 启动应用
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', 'monkey', '-p', app_name, '-c', 'android.intent.category.LAUNCHER', '1'])
        logging.info(f"{app_name} 已重启")
        return None

    # 新增操作方法
    @staticmethod
    def tap(x, y):
        """点击屏幕坐标 (x, y)"""
        command = f'input tap {x} {y}'
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', command])

    @staticmethod
    def long_press(x, y, duration=1000):
        """长按屏幕坐标 (x, y)，持续时间毫秒"""
        # 使用 input swipe 模拟长按，起点和终点相同，持续时间即为长按时间
        command = f'input swipe {x} {y} {x} {y} {duration}'
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', command])

    @staticmethod
    def swipe(x1, y1, x2, y2, duration=300):
        """滑动从 (x1, y1) 到 (x2, y2)，持续时间毫秒"""
        command = f'input swipe {x1} {y1} {x2} {y2} {duration}'
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', command])

    @staticmethod
    def input_text(text):
        """输入文字"""
        # 使用ADB keyboard输入
        command = f"am broadcast -a ADB_INPUT_TEXT --es msg '{text}'"
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', command])

    @staticmethod
    def clear_text():
        """清除文本内容，模拟长按删除或多次Backspace，也可以用命令"""
        # 这里用模拟多次Backspace
        # 或者直接使用 input keyevent
        for _ in range(20):  # 假设要清除50次
            subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', 'input', 'keyevent', '67'])  # KEYCODE_DEL

    @staticmethod
    def press_back():
        """模拟返回键"""
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', 'input', 'keyevent', '4'])

    @staticmethod
    def press_home():
        """回到home"""
        subprocess.run([ADB_PATH, '-s', CLOUD_DEVICE_IP_PORT, 'shell', 'input', 'keyevent', '3'])
# 解析模型输出
def parse_action(text):
    # 去除前后空白字符（包括换行、空格等）
    text = text.strip()
    # 去除Action:
    match = re.search(r'Action:\s*(.*)', text)
    if match:
        operation = match.group(1)
        text=operation
        # 多个匹配的正则表达式
        patterns = {
            'tap': r'tap\(\s*(\d+),\s*(\d+)\s*\)',
            'text': r'text\(\s*"([^"]*)"\s*\)',
            'long_press': r'long_press\(\s*(\d+),\s*(\d+)\s*\)',
            'swipe': r'swipe\(\s*(\d+),\s*(\d+),\s*"([^"]+)",\s*"([^"]+)"\s*\)',
            'wait': r'wait\(\s*\)',
            'FINISH': r'FINISH'
        }
        
        for action_type, pattern in patterns.items():
            match = re.match(pattern, text)
            if match:
                # 根据不同的action类型，返回不同的参数结构
                if action_type == 'tap':
                    x, y = match.groups()
                    return {'action': 'tap', 'x': int(x), 'y': int(y)}
                elif action_type == 'text':
                    content = match.group(1)
                    return {'action': 'text', 'value': content}
                elif action_type == 'long_press':
                    x, y = match.groups()
                    return {'action': 'long_press', 'x': int(x), 'y': int(y)}
                elif action_type == 'swipe':
                    x, y, direction, dist = match.groups()
                    return {
                        'action': 'swipe', 
                        'x': int(x), 
                        'y': int(y), 
                        'direction': direction, 
                        'distance': dist
                    }
                elif action_type == 'wait':
                    return {'action': 'wait'}
                elif action_type == 'FINISH':
                    return {'action': 'FINISH'}
        return None  # 如果没有匹配到
    return None
def format_action(action_data):
    """
    将 parse_action 生成的字典对象转换回 "Action: ..." 格式的字符串。
    
    :param action_dict: 一个包含 action 信息的字典，例如 {'action': 'tap', 'x': 100, 'y': 200}
    :return: 格式化后的字符串，例如 "Action: tap(100, 200)"，如果输入无效则返回 None
    """
    if not isinstance(action_data, dict) or 'action' not in action_data:
        return None  # 如果输入不是字典或没有 'action' 键，则返回 None

    action_type = action_data.get('action')
    operation_str = ""

    if action_type == 'tap':
        operation_str = f"tap({action_data['x']}, {action_data['y']})"

    
    elif action_type == 'text':
        # 注意：为了和原始格式匹配，字符串值需要用双引号包裹
        operation_str = f'text("{action_data["value"]}")'
        
    elif action_type == 'long_press':
        operation_str = f"long_press({action_data['x']}, {action_data['y']})"

    elif action_type == 'swipe':
        # 注意：direction 和 distance 也需要用双引号包裹
        operation_str = (
            f'swipe({action_data["x"]}, {action_data["y"]}, '
            f'"{action_data["direction"]}", "{action_data["distance"]}")'
        )

    elif action_type == 'wait':
        operation_str = "wait()"
        
    elif action_type == 'FINISH':
        operation_str = "FINISH"
        
    else:
        # 如果 action 类型未知，返回 None
        return None
    print(operation_str)
    return f"Action: {operation_str}"

# 标注数据存储
class AnnotationStore:
    def __init__(self):
        self.data_file = 'annotations.json'

    def save(self, data):
        with open(self.data_file, 'a') as f:
            f.write(json.dumps(data) + '\n')
@app.route('/get-screenshot',methods=['GET'])
def get_screenshot():
    # 获取屏幕截图
    # 本地图片路径
    # image_path = 'screenshots/screen.png'
    image_path=ADBController.get_screenshot()
    with open(image_path, 'rb') as f:
        image_bytes = f.read()
    # 转成 base64
    encoded_string = base64.b64encode(image_bytes).decode('utf-8')
    return jsonify({
        'status': 'success',
        'message': 'Screenshot retrieved successfully',
        'data': f'data:image/png;base64,{encoded_string}'
    })

@app.route('/restart-app',methods=['POST'])
def restart_app():
    # 重启app
    ADBController.restart_app()
    return jsonify({'status':'success','data':None,'message':"重启成功"})
# 根据任务类型填充不同的模版
def fill_templete_by_task(slot_info):
    if(slot_info.get('task_type','')=='shopping'):
        # return SHOPPING_QUESTION_PROMPT.format(
        #     task_type='shopping',
        #     app_name=slot_info.get('app_name',''),
        #     item_name=slot_info.get('item_name',''),
        #     store_name=slot_info.get('store_name',''),
        #     specs=slot_info.get('specs',''),
        #     quantity=slot_info.get('quantity',1)
        # )
        return get_task_prompt(
            task_type='shopping',
            app_name=slot_info.get('app_name'),
            item_name=slot_info.get('item_name',''),
            store_name=slot_info.get('store_name',''),
            specs=slot_info.get('specs',''),
            quantity=slot_info.get('quantity',1)
        )
    return ''
# 填充
# 构建请求体
def build_payload(image_base64, query):
    return {
        "model": "qwen2.5-vl-meituan", # 模型名称需与你部署的保持一致
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_base64}},
                    {"type": "text", "text": query}
                ]
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.7
    }
@app.route('/infer',methods=['POST'])
def infer():
    # 推理
    # 传参:image_base64,base_64编码的png图片内容;slot_info:任务相关内容,根据任务的不同参数不同,具体参考prompt_templates.py中的模版
    # 1. 获取前端传来的 Base64 图片和其他参数
    data = request.get_json()
    base64_str = data['image_base64']  # 格式如 "data:image/png;base64,xxxxx"
    slot_info=data.get('slot_info',{})
    # 2. 填充query
    query=fill_templete_by_task(slot_info=slot_info)
    # 3. 构建prompt
    payload=build_payload(base64_str,query=query)
    # 4. 请求模型
    # 发送请求
    response = requests.post(API_URL, json=payload, timeout=30)
    response.raise_for_status()  # 抛出HTTP错误
    
    # 解析结果
    result = response.json()
    content = result['choices'][0]['message']['content']
    logging.debug(f"✅  响应内容:\n{content}")
    content=parse_action(content)
    return jsonify({
        'status':'success',
        'data':content,
        'message':'模型成功响应'
    })
def append_to_json_file(data_to_append, filename):
    """
    向一个JSON文件追加一个JSON对象（字典）。
    文件内容被维护为一个JSON数组。

    :param data_to_append: 要追加的Python字典。
    :param filename: JSON文件的路径。
    """
    
    # 步骤1: 检查文件是否存在且不为空
    if os.path.exists(filename) and os.path.getsize(filename) > 0:
        try:
            # 文件存在，读取现有数据
            with open(filename, 'r', encoding='utf-8') as f:
                data_list = json.load(f)
            
            # 确保读取的数据是列表
            if not isinstance(data_list, list):
                print(f"错误: 文件 '{filename}' 的内容不是一个JSON数组。正在覆盖为新数组。")
                data_list = []
        
        except json.JSONDecodeError:
            # 文件内容不是有效的JSON，可能已损坏或为空
            print(f"警告: 无法解析 '{filename}'。将创建一个新的文件。")
            data_list = []
    else:
        # 文件不存在或为空，初始化一个空列表
        data_list = []

    # 步骤2: 将新数据追加到列表中
    data_list.append(data_to_append)

    # 步骤3: 将更新后的列表写回文件
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            # json.dump() 写入文件
            # indent=4 让JSON格式化，更易读
            # ensure_ascii=False 确保中文字符能正确显示
            json.dump(data_list, f, indent=4, ensure_ascii=False)
        print(f"数据成功追加到 {filename}")
    except IOError as e:
        print(f"错误: 无法写入文件 {filename}。{e}")
def save_base64_to_png(base64_string, output_dir, filename=None):
    """
    解码Base64字符串并将其保存为PNG图片文件。

    :param base64_string: Base64编码的字符串 (可以带 'data:image/png;base64,' 前缀)。
    :param output_dir: 图片要保存的目录。
    :param filename: (可选) 保存的文件名。如果未提供，将生成一个唯一的UUID文件名。
    :return: 成功时返回完整的文件路径，失败时返回None。
    # 这是一个 1x1 像素的透明PNG的Base64编码，非常适合用于测试
    # 格式1: 带 "Data URL" 前缀
    sample_base64_with_prefix = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

    # 格式2: 不带前缀的纯Base64字符串
    sample_base64_pure = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

    # 指定输出目录
    output_directory = "saved_images"

    print("--- 测试1: 带前缀的Base64，自动生成文件名 ---")
    save_base64_to_png(sample_base64_with_prefix, output_directory)

    print("\n--- 测试2: 不带前缀的Base64，指定文件名 ---")
    save_base64_to_png(sample_base64_pure, output_directory, filename="my_first_image.png")

    print("\n--- 测试3: 错误格式的Base64 ---")
    invalid_base64 = "this_is_not_base64"
    save_base64_to_png(invalid_base64, output_directory)
    """
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. 使用正则表达式去除 "data:image/png;base64," 前缀
    #    这使得函数对于两种格式的输入都能正常工作
    try:
        # re.sub finds the pattern and replaces it with an empty string
        pure_base64_str = re.sub(r'^data:image/.+;base64,', '', base64_string)
        
        # 2. 解码Base64字符串
        image_data = base64.b64decode(pure_base64_str)
    except (ValueError, base64.binascii.Error) as e:
        print(f"错误: Base64 解码失败 - {e}")
        return None

    # 3. 确定文件名
    if filename:
        # 如果提供了文件名，确保它是安全的
        if not filename.lower().endswith('.png'):
            filename += '.png'
    else:
        # 如果未提供文件名，生成一个唯一的UUID文件名
        filename = f"{uuid.uuid4()}.png"
        
    # 构造完整的文件路径
    file_path = os.path.join(output_dir, filename)

    # 4. 以二进制写入模式保存文件
    try:
        with open(file_path, 'wb') as f:
            f.write(image_data)
        print(f"图片成功保存到: {file_path}")
        return file_path
    except IOError as e:
        print(f"错误: 文件写入失败 - {e}")
        return None
# slot_info: 任务信息
# action_info: 动作信息
# user_id: 用户id----不同的用户分开保存;后期还可以进行统计
# image_base64: 64位格式的图片信息
@app.route('/save-annotation', methods=['POST'])
def save_annotation():
    """
        保存标注信息
    """
    data = request.get_json()
    slot_info=data['slot_info']
    action_info=data['action_info']
    user_id=data.get('user_id','default')# 如果未填,则保存到default文件中
    image_base64=data['image_base64']
    query=fill_templete_by_task(slot_info=slot_info)
    action=format_action(action_data=action_info)
    image_name=f"{uuid.uuid4()}.png"
    prompt =   {
        "messages": [
        {
            "role": "user",
            "content": f"""{query}"""
        },
        {
            "role": "assistant",
            "content": f"""{action}"""
        }
        ],
        "images": [
            f"imgs_all/{image_name}"
        ]
    }
    # 保存图片到指定路径
    save_base64_to_png(image_base64,IMGS_PATH,image_name)
    # 保存数据到指定路径
    append_to_json_file(prompt,os.path.join(BASE_ANNO_PATH,f'{user_id}.json'))
    return jsonify({'status': 'success','data':None,'message':'成功保存标注并执行'})

@app.route('/execute-action', methods=['POST'])
def execute_action():
    action_info = request.json['action_info']
    # 示例：执行adb点击操作
    if action_info['action']=='tap':
        ADBController.tap(action_info['x'],action_info['y'])
    elif action_info['action']=='long_press':
        ADBController.long_press(action_info['x'],action_info['y'],2000)
    elif action_info['action']=='swipe':
        x=action_info['x']
        y=action_info['y']
        base_dist = 200
        x_end=x
        y_end=y
        direction=action_info['direction']
        dist = action_info['distance']
        rate = 3 if dist=='long' else 2 if dist=='medium' else 1
        if direction == 'up':
            x_end = x
            y_end = y-base_dist*rate
        elif direction == 'down':
            x_end = x
            y_end = y+base_dist*rate
        elif direction == 'left':
            x_end = x-base_dist*rate
            y_end = y
        elif direction == 'right':
            x_end = x+base_dist*rate
            y_end = y
        ADBController.swipe(x,y,x_end,y_end)
    elif action_info['action']=='text':
        ADBController.clear_text()
        ADBController.input_text(action_info['value'])
    return jsonify({'status': 'success','data':None,'message':'执行成功'})
@app.route('/test_get',methods=['GET'])
def hello_world():
    return 'Hello, World!'
@app.route('/test_post', methods=['POST'])
def test_post():
    return jsonify({
        'test':'Hello, World!'
    })

if __name__ == '__main__':
    os.makedirs('screenshots', exist_ok=True)
    app.run(debug=True,host='0.0.0.0', port=5001)
