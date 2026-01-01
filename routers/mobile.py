from fastapi import APIRouter

from fastapi import Depends
from fastapi.responses import JSONResponse
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
