import shutil

from fastapi import APIRouter

from fastapi import Depends, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import with_config
from sqlalchemy.orm import Session

from model.db import get_db, ProcessTable, UserReportTable
from model.db import WorkOrderNumberTable

app = APIRouter()


@app.get('/mobile-get-form-by-phone/')
async def mobile_get_form_by_phone(phone: str, db: Session = Depends(get_db)):
    """
    手机端通过手机号获取工单列表
    :param phone: 手机号
    :param db: 数据库会话（由依赖注入提供）
    :return: 该手机号相关的工单列表
    """
    try:
        work_order_lst = db.query(WorkOrderNumberTable).filter(
            WorkOrderNumberTable.user_phone == phone
        ).all()

        return [wo for wo in work_order_lst]

    except Exception as e:
        print(f"Error in mobile_get_form_by_phone: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get('/mobile-get-form-by-id/')
async def mobile_get_form_by_id(work_order_id: str, db: Session = Depends(get_db)):
    """
    手机端通过工单ID获取工单信息
    :param work_order_id: 工单ID
    :param db: 数据库会话（由依赖注入提供）
    :return: 工单详细信息
    """
    work_form_info = db.query(WorkOrderNumberTable).filter(
        WorkOrderNumberTable.work_order_number == work_order_id
    ).first()
    process_info = db.query(ProcessTable).filter(
        ProcessTable.work_form_id == work_order_id
    ).first()
    user_request_info = db.query(UserReportTable).filter(
        UserReportTable.report_id == work_order_id
    ).first()
    return {
        "work_order_info": work_form_info,
        "process_info": process_info,
        'user_request_info': user_request_info
    }


@app.post('/convertVoice/')
async def mobile_convert_voice(pcm_file: UploadFile = File(...)):
    """
    语音转文本接口，使用百度WebSocket实时识别
    :param pcm_file: PCM音频文件
    :return: 识别结果文本
    """
    from python_realtime_asr import const
    import uuid
    import websocket
    import json
    import threading
    import time
    import os

    print("pcm_file:",pcm_file)
    # 保存上传的文件
    file_path = None
    try:
        # 确保目录存在
        os.makedirs('static/userVoice', exist_ok=True)
        
        file_path = f'static/userVoice/{pcm_file.filename}'
        with open(file_path, 'wb') as buffer:
            shutil.copyfileobj(pcm_file.file, buffer)
    except Exception as e:
        print(f"保存文件失败: {e}")
        if pcm_file.file:
            pcm_file.file.close()
        return JSONResponse({"error": f"保存文件失败: {str(e)}"}, status_code=500)
    finally:
        if pcm_file.file:
            pcm_file.file.close()

    # 用于收集识别结果的变量
    result_text = ""
    error_message = None
    is_finished = threading.Event()  # 用于同步等待WebSocket完成
    
    def send_start_params(ws):
        """
        开始参数帧
        :param websocket.WebSocket ws:
        :return:
        """
        req = {
            "type": "START",
            "data": {
                "appid": const.APPID,  # 网页上的appid
                "appkey": const.APPKEY,  # 网页上的appid对应的appkey
                "dev_pid": const.DEV_PID,  # 识别模型
                "cuid": "yourself_defined_user_id",  # 随便填不影响使用。机器的mac或者其它唯一id，百度计算UV用。
                "sample": 16000,  # 固定参数
                "format": "pcm"  # 固定参数
            }
        }
        body = json.dumps(req)
        ws.send(body, websocket.ABNF.OPCODE_TEXT)
        print("send START frame with params:" + body)

    def send_audio(ws):
        """
        发送二进制音频数据，注意每个帧之间需要有间隔时间
        :param  websocket.WebSocket ws:
        :return:
        """
        nonlocal error_message, is_finished
        chunk_ms = 160  # 160ms的录音
        chunk_len = int(16000 * 2 / 1000 * chunk_ms)
        try:
            with open(file_path, 'rb') as f:
                pcm = f.read()

            index = 0
            total = len(pcm)
            print(f"send_audio total={total}")
            while index < total:
                end = index + chunk_len
                if end >= total:
                    # 最后一个音频数据帧
                    end = total
                body = pcm[index:end]
                ws.send(body, websocket.ABNF.OPCODE_BINARY)
                index = end
                time.sleep(chunk_ms / 1000.0)  # ws.send 也有点耗时，这里没有计算
        except Exception as e:
            print(f"发送音频数据失败: {e}")
            error_message = str(e)
            is_finished.set()  # 设置事件，允许函数返回

    def send_finish(ws):
        """
        发送结束帧
        :param websocket.WebSocket ws:
        :return:
        """
        req = {
            "type": "FINISH"
        }
        body = json.dumps(req)
        ws.send(body, websocket.ABNF.OPCODE_TEXT)
        print("send FINISH frame")

    def send_cancel(ws):
        """
        发送取消帧
        :param websocket.WebSocket ws:
        :return:
        """
        req = {
            "type": "CANCEL"
        }
        body = json.dumps(req)
        ws.send(body, websocket.ABNF.OPCODE_TEXT)
        print("send Cancel frame")

    def on_open(ws):
        """
        连接后发送数据帧
        :param  websocket.WebSocket ws:
        :return:
        """
        def run(*args):
            """
            发送数据帧
            :param args:
            :return:
            """
            try:
                send_start_params(ws)
                send_audio(ws)
                send_finish(ws)
            except Exception as e:
                print(f"发送数据失败: {e}")
                nonlocal error_message
                error_message = str(e)
                is_finished.set()  # 设置事件，允许函数返回

        threading.Thread(target=run).start()

    def on_message(ws, message):
        """
        接收服务端返回的消息
        :param ws:
        :param message: json格式，自行解析
        :return:
        """
        nonlocal result_text, error_message, is_finished
        try:
            data = json.loads(message)
            print(f"收到WebSocket消息: {data}")
            
            # 跳过心跳消息
            if data.get('type') == 'HEARTBEAT':
                return
            
            # 根据百度API的响应格式解析结果
            if 'result' in data:
                result_value = data.get('result')
                
                # result可能是字符串或列表，需要分别处理
                if isinstance(result_value, str):
                    # 如果是字符串，直接使用
                    if result_value:
                        result_text = result_value
                        print(f"识别结果: {result_text}")
                elif isinstance(result_value, list):
                    # 如果是列表，合并所有结果
                    if result_value:
                        # 检查列表项是字符串还是字典
                        if isinstance(result_value[0], dict):
                            # 列表项是字典，提取word字段
                            current_text = ''.join([item.get('word', '') for item in result_value if isinstance(item, dict)])
                        else:
                            # 列表项是字符串，直接合并
                            current_text = ''.join([str(item) for item in result_value])
                        if current_text:
                            result_text = current_text
                            print(f"识别结果: {result_text}")
            
            # 检查是否是最终结果
            if data.get('type') == 'FIN_TEXT':
                # 最终结果，更新最终文本并设置完成标志
                if 'result' in data and data.get('result'):
                    result_value = data.get('result')
                    if isinstance(result_value, str):
                        result_text = result_value
                    elif isinstance(result_value, list) and result_value:
                        if isinstance(result_value[0], dict):
                            result_text = ''.join([item.get('word', '') for item in result_value if isinstance(item, dict)])
                        else:
                            result_text = ''.join([str(item) for item in result_value])
                print(f"最终识别结果: {result_text}")
                is_finished.set()  # 设置完成标志
        except json.JSONDecodeError as e:
            print(f"JSON解析失败: {e}, message: {message}")
            error_message = f"JSON解析失败: {str(e)}"
            is_finished.set()
        except Exception as e:
            print(f"解析消息失败: {e}")
            import traceback
            traceback.print_exc()
            error_message = f"解析消息失败: {str(e)}"
            is_finished.set()

    def on_error(ws, error):
        """
        库的报错，比如连接超时
        :param ws:
        :param error: json格式，自行解析
        :return:
        """
        nonlocal error_message, is_finished
        print(f"WebSocket错误: {error}")
        error_message = str(error)
        is_finished.set()  # 设置事件，允许函数返回

    def on_close(ws, close_status_code, close_msg):
        """
        Websocket关闭
        :param websocket.WebSocket ws:
        :return:
        """
        nonlocal is_finished
        print(f"WebSocket关闭: status_code={close_status_code}, msg={close_msg}")
        is_finished.set()  # 设置事件，允许函数返回

    try:
        uri = const.URI + "?sn=" + str(uuid.uuid1())
        print(f"连接WebSocket: {uri}")
        
        ws_app = websocket.WebSocketApp(uri,
                                        on_open=on_open,  # 连接建立后的回调
                                        on_message=on_message,  # 接收消息的回调
                                        on_error=on_error,  # 库遇见错误的回调
                                        on_close=on_close)  # 关闭后的回调
        
        # 在后台线程中运行WebSocket
        ws_thread = threading.Thread(target=ws_app.run_forever)
        ws_thread.daemon = True
        ws_thread.start()
        
        # 等待WebSocket完成（最多等待30秒）
        if is_finished.wait(timeout=30):
            # WebSocket已完成
            if error_message:
                return JSONResponse({"error": error_message}, status_code=500)
            
            if result_text:
                return {"text": result_text, "status": "success"}
            else:
                return JSONResponse({"error": "未收到识别结果"}, status_code=500)
        else:
            # 超时
            ws_app.close()
            return JSONResponse({"error": "识别超时"}, status_code=500)
            
    except Exception as e:
        print(f"Error in mobile_convert_voice: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        # 清理临时文件
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"删除临时文件失败: {e}")
