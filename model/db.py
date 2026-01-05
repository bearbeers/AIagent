from datetime import datetime
from typing import Union, List

from pydantic import BaseModel
from sqlalchemy import Column, String, Integer, DateTime, Float, ForeignKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# DATA_BASE_URL = 'sqlite:///agent.db'
Engine = create_engine("sqlite:///agent.db", connect_args={"check_same_thread": False})
Session = sessionmaker(bind=Engine)
Base = declarative_base()


class WorkOrderNumberTable(Base):
    __tablename__ = "work_order_number"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    report_time = Column(DateTime, default=datetime.now())
    work_order_number = Column(String)
    severityLevel = Column(String)
    ticketType = Column(String)
    ticketCategory = Column(String)
    collaborationType = Column(String)
    responsibleUnit = Column(String)
    assistingUnit = Column(String)
    location = Column(String)
    channel = Column(String)
    contact = Column(String)
    impactRange = Column(String)
    work_content = Column(String)
    work_status = Column(String)
    work_form_score = Column(Float)
    hotspot_priority = Column(Integer, default=0)  # 保存生成处置方案时的热度值
    user_phone = Column(String, ForeignKey("user_info.user_phone"))



class WorkOrderNumber(BaseModel):
    report_time:str = datetime.now()
    work_order_number: str
    severityLevel:str
    ticketType:str
    ticketCategory:str
    collaborationType:str
    responsibleUnit:str
    assistingUnit:str
    location:str
    channel:str
    contact:str
    impactRange:str
    work_content: str
    work_status: str = '未处理'
    work_form_score: float = 0.0
    user_phone: str = None

    class ConfigDict:
        from_attributes = True


class UserReportTable(Base):
    __tablename__ = "user_report"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, nullable= True)
    report_id = Column(String, ForeignKey("work_order_number.work_order_number"))
    report_content = Column(String, nullable= True)
    report_time = Column(String, nullable= True)
    report_type = Column(String, nullable= True)
    report_status = Column(String, nullable= True)


class UserReport(BaseModel):
    user_id: Union[str, None] = None
    report_id: Union[str, None] = None
    report_content: str
    report_time: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_status: str = "未处理"
    report_type: Union[str, None] = None

    class ConfigDict:
        from_attributes = True


class UserInfoTable(Base):
    __tablename__ = "user_info"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_name = Column(String)
    user_phone = Column(String, unique=True, default='18282523984')

class UserInfo(BaseModel):
    user_name: str
    user_phone: str

    class ConfigDict:
        from_attributes = True


class ProcessTable(Base):
    __tablename__ = "process"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    work_form_id = Column(String, ForeignKey("work_order_number.work_order_number"))
    processing_content = Column(String)
    processing_result = Column(String)
    public_visit = Column(String)


class Process(BaseModel):
    work_form_id: str
    processing_content: str
    processing_result: str
    public_visit: str

    class ConfigDict:
        from_attributes = True


class WorkPlanTable(Base):
    __tablename__ = "work_plan"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    work_form_id = Column(String, ForeignKey("work_order_number.work_order_number"))
    work_plan_content = Column(String)

class WorkPlan(BaseModel):
    work_form_id: str
    work_plan_content: str

    class ConfigDict:
        from_attributes = True


class ScoreTable(Base):
    __tablename__ = "score"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    work_form_id = Column(String, ForeignKey("work_order_number.work_order_number"))
    score_content = Column(String)

class Score(BaseModel):
    work_form_id: str
    score_content: str

    class ConfigDict:
        from_attributes = True



def get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()