import datetime
import json
import uuid
import os
from aiohttp.client import ClientSession
from aiohttp.formdata import FormData
from fastapi import APIRouter, File, Form, WebSocket, Depends
from fastapi.responses import JSONResponse
from fastapi.websockets import WebSocketState
from sqlalchemy.orm import Session

from model.db import get_db, UserReport, UserReportTable, ProcessTable, WorkPlanTable, ScoreTable
from model.db import WorkOrderNumber, WorkOrderNumberTable
from utils.json_handle import get_json_string
from utils.request_pa import request_pa
from utils.save_pa_token import PaTokenManager
from utils.hot_spot import MunicipalHotspotRanker


app = APIRouter()
websocket_connections = set()
hotspot_ranker = MunicipalHotspotRanker()
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = None
pa_token_manager = PaTokenManager()
BASE_URL = os.getenv("DIFY_BASE_URL")
API_KEY = os.getenv("DIFY_API_KEY")



@app.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket):
    """WebSocket端点用于实时通知"""
    await websocket.accept()
    websocket_connections.add(websocket)

    try:
        while True:
            # 保持连接活跃
            data = await websocket.receive_text()
            # 可以处理来自客户端的消息
            await websocket.send_text(f"Echo: {data}")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        websocket_connections.discard(websocket)
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close()


async def broadcast_notification(message: dict):
    """广播通知给所有连接的WebSocket客户端"""
    if not websocket_connections:
        return

    disconnected = set()
    for connection in websocket_connections:
        try:
            await connection.send_text(json.dumps(message, ensure_ascii=False))
        except Exception as e:
            print(f"Failed to send notification: {e}")
            disconnected.add(connection)

    # 移除断开的连接
    for conn in disconnected:
        websocket_connections.discard(conn)


@app.post("/submit-issue/")
async def submit_issue(user_content: str = Form(...), db=Depends(get_db)):
    """
    接收手机端提交的问题并广播通知，同时进行热度分析
    """
    ticket_info = await gen_form(user_content)
    ticket_info = ticket_info.get("ai_reply")
    ticket_info_data = WorkOrderNumber(
        work_order_number=ticket_info.get("ticketNumber", ""),
        severityLevel=ticket_info.get("severityLevel"),
        ticketType=ticket_info.get("ticketType"),
        ticketCategory=ticket_info.get("ticketCategory"),
        collaborationType=ticket_info.get("collaborationType"),
        responsibleUnit=ticket_info.get("responsibleUnit"),
        assistingUnit=''.join(ticket_info.get("assistingUnit")),
        location=ticket_info.get("location"),
        channel=ticket_info.get("channel"),
        contact=ticket_info.get("contact"),
        impactRange=ticket_info.get("impactRange"),
        work_content=ticket_info.get("summary"),
    )
    ticket_info_entry = WorkOrderNumberTable(
        **ticket_info_data.model_dump()
    )

    db.add(ticket_info_entry)
    db.commit()
    db.refresh(ticket_info_entry)

    # 保存UserReportTable，将user_content保存为report_content，用于匹配severityLevel
    user_report = UserReport(
        report_id=ticket_info_entry.work_order_number,
        report_content=user_content,  # 保存原始用户输入，用于匹配
        report_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        report_status="未处理"
    )
    user_report_entry = UserReportTable(
        **user_report.model_dump()
    )
    db.add(user_report_entry)
    db.commit()
    pa_token_manager.work_form_number = ticket_info_data.work_order_number
    pa_token_manager.user_question = ticket_info.get("summary")

    try:
        # 确保热度分析器已初始化
        if hotspot_ranker is None:
            return JSONResponse({"error": "热度分析器未初始化"}, status_code=500)

        # 添加到热度分析器
        report_idx = hotspot_ranker.add_report(user_content)

        # 创建通知消息
        notification = {
            "id": str(uuid.uuid4()),
            "type": "new_issue",
            "title": "新问题反馈",
            "content": user_content,
            "timestamp": datetime.datetime.now().isoformat(),
            "status": "pending",
            "report_idx": report_idx
        }

        # 通过WebSocket广播（包含最新热度排行榜）
        ranking = hotspot_ranker.get_hotspot_ranking(top_k=10)
        notification["hotspot_ranking"] = [
            {"issue": str(issue), "count": int(count), "cluster_id": str(cluster_id)}
            for issue, count, cluster_id in ranking
        ]

        await broadcast_notification(notification)

        # 如果Redis可用，也发送到Redis频道
        if redis_client:
            try:
                await redis_client.publish("notifications", json.dumps(notification, ensure_ascii=False))
            except Exception as e:
                print(f"Failed to publish to Redis: {e}")

        # 直接返回成功响应，不调用gen-form接口
        return {'message': '问题已提交并通知成功', 'notification': notification,
                "severityLevel": ticket_info.get("severityLevel")}
    except Exception as e:
        print(f"Error in submit_issue: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/get-dispatch-work-orders/")
async def get_dispatch_work_orders(
        db: Session = Depends(get_db),
        limit: int = 50,
        status: str = None
):
    """
    获取待处置调度工单列表（从WorkOrderNumberTable表获取）
    :param limit: 返回工单数量限制，默认50条
    :param status: 工单状态筛选，None表示获取所有工单
    :return: 待处置调度工单列表
    """
    try:
        query = db.query(WorkOrderNumberTable).filter(
            WorkOrderNumberTable.work_order_number.isnot(None)
        )

        # 如果指定了状态，进行筛选
        if status:
            query = query.filter(WorkOrderNumberTable.work_status == status)
        else:
            # 默认获取未处理的工单
            query = query.filter(
                (WorkOrderNumberTable.work_status == '未处理') |
                (WorkOrderNumberTable.work_status.is_(None))
            )

        work_orders = query.order_by(WorkOrderNumberTable.report_time.desc()).limit(limit).all()

        result = []
        for wo in work_orders:
            # 判断紧急程度
            severity_level = wo.severityLevel or "普通类"
            if "紧急" in str(severity_level) or severity_level == "urgent":
                urgency_level = "urgent"
            elif "快速" in str(severity_level) or severity_level == "quick":
                urgency_level = "quick"
            else:
                urgency_level = "normal"

            # 格式化时间
            date_str = ""
            time_str = ""
            if wo.report_time:
                try:
                    if isinstance(wo.report_time, str):
                        if " " in wo.report_time:
                            parts = wo.report_time.split(" ")
                            date_str = parts[0]
                            time_str = parts[1][:5] if len(parts) > 1 else ""
                        elif "T" in wo.report_time:
                            parts = wo.report_time.split("T")
                            date_str = parts[0]
                            time_str = parts[1][:5] if len(parts) > 1 else ""
                    else:
                        date_str = wo.report_time.strftime("%Y-%m-%d")
                        time_str = wo.report_time.strftime("%H:%M")
                except:
                    pass

            result.append({
                "id": str(wo.id),
                "work_order_number": wo.work_order_number,
                "urgencyLevel": urgency_level,
                "category": "紧急类" if urgency_level == "urgent" else (
                    "快速处理类" if urgency_level == "quick" else "普通类"),
                "type": wo.ticketType or "问题类型",
                "ticketCategory": wo.ticketCategory or "候车时间长",
                "date": date_str,
                "time": time_str,
                "responsibleUnit": wo.responsibleUnit or "待分配",
                "summary": wo.work_content or "",
                "priority": 0,  # 可以根据需要计算优先级
                "severityLevel": severity_level,
                "location": wo.location or "",
                "collaborationType": wo.collaborationType or "跨单位协同处置",
                "assistingUnit": wo.assistingUnit or "待分配",
                "reportingChannel": wo.channel or "热线电话/移动端",
                "contactPhone": wo.contact or "待补充",
                "hotlineContact": wo.contact or "待补充",
                "impactRange": wo.impactRange or "",
                "work_status": wo.work_status or "未处理",
            })

        # 按紧急程度和报告时间排序
        urgency_order = {"urgent": 3, "quick": 2, "normal": 1}
        sorted_result = sorted(result, key=lambda x: (
            urgency_order.get(x["urgencyLevel"], 0),
            x["date"] + " " + x["time"]
        ), reverse=True)

        return {"work_orders": sorted_result}
    except Exception as e:
        print(f"Error in get_dispatch_work_orders: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/get-work-order-by-issue/")
async def get_work_order_by_issue(issue: str, db: Session = Depends(get_db)):
    """
    根据问题内容从WorkOrderNumberTable获取完整的工单信息
    :param db:
    :param issue: 问题内容（issue text）
    :return: 完整的工单信息
    """
    try:
        # 优先直接从WorkOrderNumberTable查询，使用work_content字段匹配
        # 只查询work_status为"未处理"的工单
        # 方法1：精确匹配work_content
        work_order = db.query(WorkOrderNumberTable).filter(
            WorkOrderNumberTable.work_content == issue,
            (WorkOrderNumberTable.work_status == '未处理') | (WorkOrderNumberTable.work_status.is_(None))
        ).first()

        if not work_order:
            # 方法2：模糊匹配work_content
            work_order = db.query(WorkOrderNumberTable).filter(
                WorkOrderNumberTable.work_content.like(f"%{issue}%"),
                (WorkOrderNumberTable.work_status == '未处理') | (WorkOrderNumberTable.work_status.is_(None))
            ).first()

        if not work_order:
            # 方法3：通过UserReportTable关联查询
            from model.db import UserReportTable
            user_report = db.query(UserReportTable).filter(
                UserReportTable.report_content == issue
            ).first()

            if user_report and user_report.report_id:
                # 通过report_id（即work_order_number）查找对应的工单，且work_status为"未处理"
                work_order = db.query(WorkOrderNumberTable).filter(
                    WorkOrderNumberTable.work_order_number == user_report.report_id,
                    (WorkOrderNumberTable.work_status == '未处理') | (WorkOrderNumberTable.work_status.is_(None))
                ).first()

        if not work_order:
            # 方法4：模糊匹配UserReportTable的report_content，然后关联查询
            from model.db import UserReportTable
            user_reports = db.query(UserReportTable).filter(
                UserReportTable.report_content.like(f"%{issue}%")
            ).all()

            for user_report in user_reports:
                if user_report.report_id:
                    work_order = db.query(WorkOrderNumberTable).filter(
                        WorkOrderNumberTable.work_order_number == user_report.report_id,
                        (WorkOrderNumberTable.work_status == '未处理') | (WorkOrderNumberTable.work_status.is_(None))
                    ).first()
                    if work_order:
                        break

        if work_order:
            # 返回WorkOrderNumberTable中的所有字段
            # 设置工单编号到token管理器，供get-solution接口使用
            pa_token_manager.user_question = work_order.work_content
            return {
                "work_order_number": work_order.work_order_number,
                "severityLevel": work_order.severityLevel,
                "ticketType": work_order.ticketType,
                "ticketCategory": work_order.ticketCategory,
                "collaborationType": work_order.collaborationType,
                "responsibleUnit": work_order.responsibleUnit,
                "assistingUnit": work_order.assistingUnit,
                "location": work_order.location,
                "channel": work_order.channel,
                "contact": work_order.contact,
                "impactRange": work_order.impactRange,
                "work_content": work_order.work_content,
                "work_status": work_order.work_status,
                "report_time": work_order.report_time.strftime("%Y-%m-%d %H:%M:%S") if work_order.report_time else None,
            }

        return JSONResponse({"error": "未找到对应的工单信息"}, status_code=404)
    except Exception as e:
        print(f"Error in get_work_order_by_issue: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/hotspot-ranking/")
async def get_hotspot_ranking(top_k: int = 10, refresh: bool = True):
    """
    获取市政基础设施问题热度排行榜
    :param top_k: 返回前多少条热度问题，默认10条
    :param refresh: 是否从数据库重新加载数据，默认True（确保数据最新）
    :return: 热度排行榜列表
    """
    try:
        # 确保热度分析器已初始化
        if hotspot_ranker is None:
            return JSONResponse({"error": "热度分析器未初始化"}, status_code=500)

        # 默认每次都从数据库重新加载，确保数据最新
        if refresh:
            db_session = next(get_db())
            try:
                hotspot_ranker.reload_from_database(db_session)
            finally:
                db_session.close()

        ranking = hotspot_ranker.get_hotspot_ranking(top_k=top_k)
        stats = hotspot_ranker.get_statistics()

        # 从数据库获取severityLevel信息
        db_session = next(get_db())
        try:
            # 查询所有工单的work_content和severityLevel
            work_orders = db_session.query(WorkOrderNumberTable).filter(
                WorkOrderNumberTable.work_content.isnot(None),
                WorkOrderNumberTable.severityLevel.isnot(None)
            ).all()

            # 建立work_content到severityLevel的映射（支持模糊匹配）
            work_content_to_severity = {}
            for wo in work_orders:
                if wo.work_content and wo.severityLevel:
                    work_content_to_severity[wo.work_content] = wo.severityLevel

            # 同时查询UserReportTable，建立report_content到severityLevel的映射
            # 因为热度分析器使用的是user_content（report_content），而不是work_content（summary）
            from model.db import UserReportTable
            user_reports = db_session.query(UserReportTable).filter(
                UserReportTable.report_content.isnot(None),
                UserReportTable.report_content != ''
            ).all()

            # 建立report_content（user_content）到severityLevel的映射
            # 通过report_id关联到WorkOrderNumberTable
            user_content_to_severity = {}
            for ur in user_reports:
                if ur.report_content and ur.report_id:
                    # 通过report_id找到对应的工单
                    work_order = db_session.query(WorkOrderNumberTable).filter(
                        WorkOrderNumberTable.work_order_number == ur.report_id
                    ).first()
                    if work_order and work_order.severityLevel:
                        user_content_to_severity[ur.report_content] = work_order.severityLevel

            # 获取聚类信息，用于匹配
            clusters = hotspot_ranker.get_clusters()
        finally:
            db_session.close()

        # 构建返回结果，包含severityLevel
        ranking_result = []
        for idx, (issue, count, cluster_id) in enumerate(ranking):
            # 尝试从映射中获取severityLevel
            severity_level = None

            # 方法1：精确匹配（优先使用user_content映射，因为热度分析器使用的是user_content）
            if issue in user_content_to_severity:
                severity_level = user_content_to_severity[issue]
            elif issue in work_content_to_severity:
                severity_level = work_content_to_severity[issue]
            else:
                # 方法2：通过聚类中的报告匹配（聚类中的报告是user_content）
                if str(cluster_id) in clusters:
                    cluster_reports = clusters[str(cluster_id)].get('reports', [])
                    # 对于聚类中的每个报告（user_content），尝试匹配工单
                    for report in cluster_reports:
                        # 优先使用user_content映射（精确匹配）
                        if report in user_content_to_severity:
                            severity_level = user_content_to_severity[report]
                            break
                        # 其次使用work_content映射（精确匹配）
                        elif report in work_content_to_severity:
                            severity_level = work_content_to_severity[report]
                            break
                        # 模糊匹配：检查工单内容是否包含报告，或报告是否包含工单内容
                        for content, level in user_content_to_severity.items():
                            if len(report) > 0 and len(content) > 0:
                                if report in content or content in report:
                                    severity_level = level
                                    break
                        if not severity_level:
                            for content, level in work_content_to_severity.items():
                                if len(report) > 0 and len(content) > 0:
                                    if report in content or content in report:
                                        severity_level = level
                                        break
                        if severity_level:
                            break

                # 方法3：模糊匹配问题文本
                if not severity_level:
                    # 优先使用user_content映射
                    for content, level in user_content_to_severity.items():
                        if issue in content or content in issue:
                            severity_level = level
                            break
                    # 其次使用work_content映射
                    if not severity_level:
                        for content, level in work_content_to_severity.items():
                            if issue in content or content in issue:
                                severity_level = level
                                break

            ranking_result.append({
                "rank": idx + 1,
                "issue": issue,
                "count": count,
                "cluster_id": cluster_id,
                "severityLevel": severity_level  # 添加severityLevel字段
            })

        return {
            "ranking": ranking_result,
            "statistics": stats
        }
    except Exception as e:
        print(f"Error in get_hotspot_ranking: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/hotspot-clusters/")
async def get_hotspot_clusters():
    """
    获取所有问题聚类信息
    :return: 所有聚类信息
    """
    try:
        # 确保热度分析器已初始化
        if hotspot_ranker is None:
            return JSONResponse({"error": "热度分析器未初始化"}, status_code=500)

        clusters = hotspot_ranker.get_clusters()
        return {"clusters": clusters}
    except Exception as e:
        print(f"Error in get_hotspot_clusters: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/audio-to-text")
async def audio_to_text(audio_file: bytes = File(...)):
    """
    语音转文本
    :param audio_file: 语音文件
    :return: 语音文本内容
    """
    try:
        url = "/audio-to-text"
        async with ClientSession() as session:
            data_form = FormData()
            data_form.add_field(
                'file',
                audio_file,
                content_type="audio/mp3"
            )
            data_form.add_field(
                "user",
                "user_abc"
            )
            async with session.post(
                    BASE_URL + url,
                    data=data_form,
                    headers={"Authorization": f'Bearer {API_KEY}'}
            ) as resp:
                if resp.status == 200:
                    response_json = await resp.json(encoding="utf-8")
                    pa_token_manager.user_question = response_json.get('text', '')
                    return response_json
                else:
                    error_text = await resp.text()
                    print(f"音频转写失败: {resp.status}, {error_text}")
                    return JSONResponse(
                        {"error": f"转写失败: {resp.status}", "message": error_text},
                        status_code=resp.status
                    )
    except Exception as e:
        print(f"Error in audio_to_text: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/gen-form/")
async def gen_form(user_content: str = Form(None)) -> dict:
    """
    调用PA agent,生成工单
    :return:
    """
    if user_content is None:
        user_content = pa_token_manager.user_question
    else:
        pa_token_manager.user_question = user_content

    url = "/basic/openapi/engine/chat/v1/completions"
    data = {
        "chatId": "1414934653136449536",
        "appId": "a4f80bb2f25b4f65bd8a0fbaa813d0c9",
        "messages": [
            {
                "content": "市政基础设施上报。" + user_content,
                "role": "user"
            }
        ]
    }

    res_json = request_pa(os.getenv('PA_BASE_URL') + url, data, token=await pa_token_manager.get_token())
    ai_reply = res_json['choices'][0]['message']['content']

    # 解析AI回复，获取工单数据
    try:
        work_order_data = get_json_string(ai_reply)
        # 保存工单编号到token管理器
        pa_token_manager.work_form_number = work_order_data.get("ticketNumber", "")
        # 保存工单信息到token管理器，用于后续评分使用
        pa_token_manager.form_info = work_order_data
    except Exception as e:
        print(f"Error parsing AI reply: {e}")
        work_order_data = {
            "reportTime": datetime.datetime.now().isoformat(),
            "ticketNumber": "解析失败",
            "severityLevel": "未知",
            "ticketType": "未知",
            "ticketCategory": "未知",
            "collaborationType": "未知",
            "responsibleUnit": "未知部门",
            "assistingUnit": [],
            "location": "未知位置",
            "channel": "手机",
            "contact": "需补充联系人信息",
            "summary": user_content,
            "impactRange": "影响范围未知"
        }

    # 广播工单生成消息到所有WebSocket客户端
    work_order_notification = {
        "type": "work_order_created",
        "ticket_number": work_order_data.get("ticketNumber", "未知编号"),
        "department": work_order_data.get("responsibleUnit", "未知部门"),
        "content": work_order_data.get("summary", user_content),
        "timestamp": work_order_data.get("reportTime", datetime.datetime.now().isoformat()),
        "severity_level": work_order_data.get("severityLevel", "未知"),
        "ticket_type": work_order_data.get("ticketType", "未知"),
        "location": work_order_data.get("location", "未知位置"),
        "impact_range": work_order_data.get("impactRange", "未知")
    }
    await broadcast_notification(work_order_notification)

    # 保存工单信息到数据库

    user_report = UserReport(
        user_id='',
        report_id=work_order_data.get("ticketNumber", "未知编号"),
        report_content=work_order_data.get("summary", user_content),
        report_time=work_order_data.get("reportTime", datetime.datetime.now().isoformat()),
        report_type="工单"
    )
    db_session = next(get_db())
    try:
        user_report_entry = UserReportTable(
            user_id=user_report.user_id,
            report_id=user_report.report_id,
            report_content=user_report.report_content,
            report_time=user_report.report_time,
            # report_status=user_report.report_status,
            report_type=user_report.report_type
        )
        # db_session.add(work_order_entry)
        db_session.add(user_report_entry)
        db_session.commit()
        # db_session.refresh(work_order_entry)
        db_session.refresh(user_report_entry)
    finally:
        db_session.close()

    return {'ai_reply': work_order_data}


@app.get("/get-weather")
async def get_weather():
    """
    默认获取遂宁市的天气
    :return:
    """
    weather_base_url = os.getenv("WEATHER_BASE_URL")
    url = "/v7/weather/now"
    async with ClientSession() as session:
        params = {'location': "101270701"}
        async with session.get(
                weather_base_url + url,
                params=params, headers={"X-QW-Api-Key": os.getenv("WEATHER_API_KEY")}
        ) as resp:
            if resp.status == 200:
                response_json = await resp.json(encoding="utf-8")
                return response_json
            else:
                return {"message": "Something went wrong."}


@app.get("/get-holiday/")
async def get_holiday():
    """
    获取当天是否为节假日、工作日
    :return: statusDesc：节假日、工作日
    """
    url = "http://apis.juhe.cn/fapig/calendar/day"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    params = {
        "key": os.getenv("HOLIDAY_API_KEY"),
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
    }
    async with ClientSession() as session:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 200:
                response_json = await resp.json(encoding="utf-8")
                return response_json['result']["statusDesc"]
            else:
                return {"message": "Something went wrong."}


@app.post("/get-solution/")
async def get_solution(work_order_content: str = Form(None), work_order_number:str=Form(None)):
    """
    获取解决方案
    :param work_order_content: 工单内容（可选，如果不提供则使用pa_token_manager.user_question）
    :return: ai回复
    """
    try:
        # 优先使用传入的工单内容，否则使用pa_token_manager中的user_question
        user_content = work_order_content or pa_token_manager.user_question or ""

        if not user_content:
            return JSONResponse({"error": "工单内容不能为空"}, status_code=400)
        db_session = next(get_db())
        work_order_entry = (db_session.query(WorkOrderNumberTable)
                            .filter(WorkOrderNumberTable.work_content == pa_token_manager.user_question)
                            .all())
        if work_order_entry:
            for entry in work_order_entry:
                entry.work_status = "处理中"
                db_session.commit()

        url = "/basic/openapi/engine/chat/v1/completions"
        weather_info = await get_weather()
        holiday_info = await get_holiday()
        data = {
            "chatId": "1414934653136449536",
            "appId": "a4f80bb2f25b4f65bd8a0fbaa813d0c9",
            "messages": [
                {
                    "content": "生成处置流程和处理方案." + user_content + f"当地天气信息：{weather_info.get('now', {})},当天是否为节假日:{holiday_info}",
                    "role": "user"
                }
            ]
        }

        res_json = request_pa(os.getenv('PA_BASE_URL') + url, data, token=await pa_token_manager.get_token())
        ai_reply = res_json['choices'][0]['message']['content']
        ai_reply_json = get_json_string(ai_reply)
        save_solution = db_session.query(WorkPlanTable).filter(
            WorkPlanTable.work_form_id == work_order_number
        ).first()
        if save_solution:
            save_solution.work_plan_content = ai_reply
            db_session.commit()
        return ai_reply_json

    except Exception as e:
        print(f"Error in get_solution: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/get-judge/")
async def get_judge(process_result:str, process_content:str,public_visit:str, work_order_number: str):
    """
    获取工单评分
    :process_result:处理结果
    :process_content:处置过程
    :public_visit:群众回访
    :param work_order_number:工单编号
    :return:
    """
    form_info = pa_token_manager.form_info or None
    data = {
        "chatId": "1414934653136449536",
        "appId": "a4f80bb2f25b4f65bd8a0fbaa813d0c9",
        "messages": [
            {
                "content": "工单评价." + public_visit + f'工单信息：{form_info}。+ 返回的json数据，键用英文表示，值用中文表示',
                "role": "user"
            }
        ]
    }
    url = "/basic/openapi/engine/chat/v1/completions"
    res_json = request_pa(os.getenv('PA_BASE_URL') + url, data, token=await pa_token_manager.get_token())
    ai_reply = res_json['choices'][0]['message']['content']
    json_string = get_json_string(ai_reply)

    # 获取工单内容并更新工单状态为"已处理"
    score_work = json_string.get("WorkOrderRating")

    if work_order_number:
        # 获取数据库会话并更新工单状态
        db_session = next(get_db())

        try:
            # 查询工单记录
            work_order_entry = db_session.query(WorkOrderNumberTable).filter(
                WorkOrderNumberTable.work_order_number == work_order_number
            ).all()
            process_info = db_session.query(ProcessTable).filter(
                ProcessTable.work_form_id == work_order_number
            ).all()
            save_score = db_session.query(ScoreTable).filter(
                ScoreTable.work_form_id == work_order_number
            ).first()
            if save_score:
                save_score.score_content = json_string
                db_session.commit()
            for entry in work_order_entry:
                if entry:
                    # 更新工单状态为"已处理"
                    entry.work_status = "已处理"
                    entry.work_form_score = score_work['OverallScore']
                    db_session.commit()
            for entry in process_info:
                if entry:
                    entry.processing_result = process_result
                    entry.processing_content = process_content
                    entry.public_visit = public_visit
                    db_session.commit()
        finally:
            db_session.close()
    return JSONResponse(json_string)


@app.post("/save-work-order-number/")
def save_work_order_number(
        work_order_number: WorkOrderNumber,
        db: Session = Depends(get_db)
):
    """
    保存工单编号
    :param db:
    :param work_order_number: 工单编号
    :return:
    """
    work_order_number = WorkOrderNumberTable(
        work_order_number=work_order_number.work_order_number,
        # status=work_order_number.status
    )
    db.add(work_order_number)
    db.commit()
    db.refresh(work_order_number)
    return work_order_number


@app.get("/get-work-order-status/")
def get_work_order_status(
        work_order_number: str,
        db: Session = Depends(get_db)
):
    """
    获取工单状态
    :param db:
    :param work_order_number: 工单编号
    :return:
    """
    work_order_number = db.query(WorkOrderNumberTable).filter(
        WorkOrderNumberTable.work_order_number == work_order_number
    ).first()
    if not work_order_number:
        return {"status": "未找到工单"}
    return work_order_number


@app.get("/get-work-order-no-score/")
async def get_work_order_no_score(
        db: Session = Depends(get_db),
        limit: int = 50
):
    """
    获取待评分工单列表（work_form_score为None或0的工单）
    :param db:
    :param limit: 返回工单数量限制，默认50条
    :return: 待评分工单列表
    """
    try:
        # 查询work_form_score为None或0的工单
        work_orders = db.query(WorkOrderNumberTable).filter(
            (WorkOrderNumberTable.work_form_score.is_(None)) |
            (WorkOrderNumberTable.work_form_score == 0.0)
        ).order_by(WorkOrderNumberTable.report_time.desc()).limit(limit).all()

        result = []
        for wo in work_orders:
            # 判断紧急程度
            severity_level = wo.severityLevel or "普通类"
            if "紧急" in str(severity_level) or severity_level == "urgent":
                urgency_level = "urgent"
            elif "快速" in str(severity_level) or severity_level == "quick":
                urgency_level = "quick"
            else:
                urgency_level = "normal"

            # 格式化时间
            date_str = ""
            time_str = ""
            if wo.report_time:
                try:
                    if isinstance(wo.report_time, str):
                        if " " in wo.report_time:
                            parts = wo.report_time.split(" ")
                            date_str = parts[0]
                            time_str = parts[1][:5] if len(parts) > 1 else ""
                        elif "T" in wo.report_time:
                            parts = wo.report_time.split("T")
                            date_str = parts[0]
                            time_str = parts[1][:5] if len(parts) > 1 else ""
                    else:
                        date_str = wo.report_time.strftime("%Y-%m-%d")
                        time_str = wo.report_time.strftime("%H:%M")
                except:
                    pass

            result.append({
                "id": str(wo.id),
                "work_order_number": wo.work_order_number,
                "urgencyLevel": urgency_level,
                "category": "紧急类" if urgency_level == "urgent" else (
                    "快速处理类" if urgency_level == "quick" else "普通类"),
                "type": wo.ticketType or "问题类型",
                "ticketCategory": wo.ticketCategory or "候车时间长",
                "date": date_str,
                "time": time_str,
                "responsibleUnit": wo.responsibleUnit or "待分配",
                "summary": wo.work_content or "",
                "priority": 99 if urgency_level == "urgent" else 0,  # 紧急工单显示99
                "severityLevel": severity_level,
                "location": wo.location or "",
                "collaborationType": wo.collaborationType or "跨单位协同处置",
                "assistingUnit": wo.assistingUnit or "待分配",
                "reportingChannel": wo.channel or "热线电话/移动端",
                "contactPhone": wo.contact or "待补充",
                "hotlineContact": wo.contact or "待补充",
                "impactRange": wo.impactRange or "",
                "work_status": wo.work_status or "未处理",
                "work_form_score": wo.work_form_score or 0.0,
            })

        # 按紧急程度和报告时间排序
        urgency_order = {"urgent": 3, "quick": 2, "normal": 1}
        sorted_result = sorted(result, key=lambda x: (
            urgency_order.get(x["urgencyLevel"], 0),
            x["date"] + " " + x["time"]
        ), reverse=True)

        return {"work_orders": sorted_result}
    except Exception as e:
        print(f"Error in get_work_order_no_score: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/get-work-order-scored/")
async def get_work_order_scored(
        db: Session = Depends(get_db),
        limit: int = 50
):
    """
    获取已评分工单列表（work_form_score不为None且不为0的工单）
    :param db:
    :param limit: 返回工单数量限制，默认50条
    :return: 已评分工单列表
    """

    try:
        scored_orders = db.query(WorkOrderNumberTable).filter(
            WorkOrderNumberTable.work_form_score.isnot(None),
            WorkOrderNumberTable.work_form_score != 0.0
        ).order_by(WorkOrderNumberTable.report_time.desc()).limit(limit).all()

        result = []
        for wo in scored_orders:
            # 判断紧急程度
            severity_level = wo.severityLevel or "普通类"
            if "紧急" in str(severity_level) or severity_level == "urgent":
                urgency_level = "urgent"
            elif "快速" in str(severity_level) or severity_level == "quick":
                urgency_level = "quick"
            else:
                urgency_level = "normal"

            # 格式化时间
            date_str = ""
            time_str = ""
            if wo.report_time:
                try:
                    if isinstance(wo.report_time, str):
                        if " " in wo.report_time:
                            parts = wo.report_time.split(" ")
                            date_str = parts[0]
                            time_str = parts[1][:5] if len(parts) > 1 else ""
                        elif "T" in wo.report_time:
                            parts = wo.report_time.split("T")
                            date_str = parts[0]
                            time_str = parts[1][:5] if len(parts) > 1 else ""
                    else:
                        date_str = wo.report_time.strftime("%Y-%m-%d")
                        time_str = wo.report_time.strftime("%H:%M")
                except:
                    pass

            result.append({
                "id": str(wo.id),
                "work_order_number": wo.work_order_number,
                "urgencyLevel": urgency_level,
                "category": "紧急类" if urgency_level == "urgent" else (
                    "快速处理类" if urgency_level == "quick" else "普通类"),
                "type": wo.ticketType or "问题类型",
                "ticketCategory": wo.ticketCategory or "候车时间长",
                "date": date_str,
                "time": time_str,
                "responsibleUnit": wo.responsibleUnit or "待分配",
                "summary": wo.work_content or "",
                "priority": 99 if urgency_level == "urgent" else 0,
                "severityLevel": severity_level,
                "location": wo.location or "",
                "collaborationType": wo.collaborationType or "跨单位协同处置",
                "assistingUnit": wo.assistingUnit or "待分配",
                "reportingChannel": wo.channel or "热线电话/移动端",
                "contactPhone": wo.contact or "待补充",
                "hotlineContact": wo.contact or "待补充",
                "impactRange": wo.impactRange or "",
                "work_status": wo.work_status or "已处理",
                "work_form_score": wo.work_form_score or 0.0,
                "efficiencyScore": wo.work_form_score or 0.0,
            })

        # 按紧急程度和报告时间排序
        urgency_order = {"urgent": 3, "quick": 2, "normal": 1}
        sorted_result = sorted(result, key=lambda x: (
            urgency_order.get(x["urgencyLevel"], 0),
            x["date"] + " " + x["time"]
        ), reverse=True)

        return {"work_orders": sorted_result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/get-work-order-detail/")
async def get_work_order_detail(
        work_order_id: str,
        db: Session = Depends(get_db)
):
    """
    获取工单详情
    :param work_order_id: 工单ID
    :param db:
    :return: 工单详情
    """
    try:
        work_order_info = db.query(WorkOrderNumberTable).filter(
            WorkOrderNumberTable.work_order_number == work_order_id
        ).first()
        process_info = db.query(ProcessTable).filter(
            ProcessTable.work_form_id == work_order_id
        ).first()
        solution_info = db.query(WorkPlanTable).filter(
            WorkPlanTable.work_form_id == work_order_id
        ).first()
        score_info = db.query(ScoreTable).filter(
            ScoreTable.work_form_id == work_order_id
        ).first()
        return {
            "work_order_info": work_order_info,
            "process_info": process_info,
            "solution_info": solution_info,
            "score_info": score_info
        }
    except Exception as e:
        print(f"Error in get_work_order_detail: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


