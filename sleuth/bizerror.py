"""Business error catalog and APPError. Messages come from BizErrorCode only."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class ResponseModel(BaseModel):
    """Uniform HTTP JSON envelope."""

    code: str
    msg: str
    data: Any = None


class BizErrorCode(Enum):
    """业务错误码定义"""

    SUC0000 = ("SUC0000", "成功")
    REQUEST_VALIDATION_FAILED = ("AMLP001", "参数校验异常, {0}")
    API_NOT_FOUND = ("AMLP002", "API {0} 不存在")
    NEED_LOGIN = ("AMLP202", "请先登录")
    AUTH_NOT_FOUND = ("AMLP203", "权限不足")
    USER_NOT_FOUND = ("AMLP301", "用户不存在")
    USR_EXIST = ("AMLP302", "用户 {0} 已存在")
    UPDATE_FAIL = ("AMLP403", "记录更新失败")
    INSERT_FAIL = ("AMLP404", "记录插入失败")
    DOWNLOAD_S3_FAIL = ("AMLQ001", "下载s3文件 {0} 失败")
    UPLOAD_S3_FAIL = ("AMLQ002", "上传s3文件 {0} 失败")
    DELETE_S3_FAIL = ("AMLQ003", "删除s3文件 {0} 失败")
    READ_FAIL = ("AMLQ004", "读取文件 {0} 失败")
    FILE_OUT_FAIL = ("AMLQ005", "获取文件到输出流失败")
    UPLOAD_NULL = ("AMLQ006", "上传文件为空")
    DOWNLOAD_FAIL = ("AMLQ007", "下载文件失败： {0}")
    AUTH_EXIST = ("AMLQ300", "权限已存在")
    AUTH_PROCESS = ("AMLQ301", "权限正在处理中")
    AUTH_NOT_PERMIT = ("AMLQ302", "权限不足")
    PYTH_API_FAIL = ("AMLQ100", "请求python-API {0} 失败")
    OTH_API_FAIL = ("AMLQ101", "请求外部API {0} 失败")
    ABNORMAL_OPERATION = ("AMLQ205", "异常操作：{0}")

    JOB_NOT_EXIST = ("AMLK001", "任务{}不存在")
    KP_API_FAIL = ("AMLK101", "请求 K+ API {0} 失败")

    KB_NOT_FOUND = ("AMLK002", '知识库"{0}"不存在')
    KB_ALREADY_EXISTS = ("AMLK003", '知识库"{0}"已存在')
    DIRECTORY_NOT_FOUND = ("AMLK004", '目录"{0}"不存在')
    DIRECTORY_ALREADY_EXISTS = ("AMLK005", '目录"{0}"已存在')
    DIRECTORY_NOT_EMPTY = ("AMLK006", '目录"{0}"下存在子目录或知识，无法删除')
    KB_NOT_EMPTY = ("AMLK007", '知识库"{0}"下存在目录或知识，无法删除')
    TAG_GROUP_NOT_FOUND = ("AMLK008", '标签组"{0}"不存在')
    TAG_GROUP_NOT_BELONG = ("AMLK009", '标签组"{0}"不属于知识库"{1}"')
    TAG_CODE_NOT_FOUND = ("AMLK010", '码值"{0}"不存在')
    ASSET_NOT_FOUND = ("AMLK011", '知识资产"{0}"不存在')
    ASSET_STATUS_NOT_EDITABLE = ("AMLK012", '知识资产"{0}"状态不允许编辑')
    ASSET_HAS_PENDING_VERSION = ("AMLK013", '知识资产"{0}"已有待审核版本')
    TARGET_VERSION_NOT_FOUND = ("AMLK014", '目标版本"{0}"不存在')
    TARGET_VERSION_NOT_IN_CHAIN = ("AMLK015", '目标版本"{0}"不属于当前资产版本链')
    SOURCE_NOT_FOUND = ("AMLK016", '来源"{0}"不存在')
    KP_NOT_ENABLED = ("AMLK017", "企业知识平台未启用")
    KP_CLIENT_CREATE_FAILED = ("AMLK018", "企业知识平台客户端创建失败")
    KP_BINDING_NOT_FOUND = ("AMLK019", '未找到 external_doc_id="{0}" 对应的绑定记录')
    KP_SYNC_FAILED = ("AMLK020", "K+ 同步失败：{0}")
    BATCH_QUERY_LIMIT = ("AMLK021", "批量查询最多支持 {0} 个资产 ID")
    PARAM_INVALID = ("AMLK022", "参数校验失败：{0}")
    FILE_UPLOAD_FAILED = ("AMLK023", "文件上传失败：{0}")

    SESSION_NOT_FOUND = ("AMLS001", "会话不存在：{0}")
    FILE_NOT_READY = ("AMLS002", "会话文件尚未就绪：{0}")
    EXTRACT_FAILED = ("AMLS003", "附件解析失败：{0}")
    AGENT_CONFIG_INVALID = ("AMLS004", "Agent 或模型配置无效：{0}")
    MCP_CALL_FAILED = ("AMLS005", "MCP 调用失败：{0}")
    TURN_FAILED = ("AMLS006", "对话轮次失败：{0}")
    MEMORY_UNAVAILABLE = ("AMLS007", "长期记忆不可用：{0}")
    API_DEPRECATED = ("AMLS008", "接口已废弃：{0}")
    ENCRYPT_NOT_CONFIGURED = ("AMLS009", "文件加密未配置：{0}")
    DIRECTORY_UNAVAILABLE = ("AMLS010", "目录服务不可用：{0}")

    def __init__(self, code: str, error_message: str):
        self._code = code
        self._error_message = error_message

    @property
    def code(self) -> str:
        return self._code

    @property
    def error_message(self) -> str:
        return self._error_message

    def format_message(self, *args) -> str:
        tmpl = self._error_message
        if not args:
            try:
                return tmpl.format()
            except (IndexError, KeyError, ValueError):
                return tmpl
        try:
            return tmpl.format(*args)
        except (IndexError, KeyError, ValueError):
            return f"{tmpl}: {args[0]}"


class APPError(Exception):
    """Domain / HTTP failure carrying a BizErrorCode."""

    def __init__(
        self,
        code: str,
        msg: str,
        status: int = 400,
        data: Any = None,
    ):
        super().__init__(msg)
        self.code = str(code)
        self.msg = str(msg)
        self.status = int(status)
        self.data = data

    @classmethod
    def of(
        cls,
        item: BizErrorCode,
        *args,
        status: int = 400,
        data: Any = None,
    ) -> "APPError":
        return cls(
            code=item.code,
            msg=item.format_message(*args),
            status=status,
            data=data,
        )

    def envelope(self) -> dict:
        return ResponseModel(code=self.code, msg=self.msg, data=self.data).model_dump()


def ok_payload(data: Any = None) -> dict:
    return ResponseModel(
        code=BizErrorCode.SUC0000.code,
        msg=BizErrorCode.SUC0000.error_message,
        data=data,
    ).model_dump()


def fail_payload(item: BizErrorCode, *args, data: Any = None) -> dict:
    return ResponseModel(
        code=item.code,
        msg=item.format_message(*args),
        data=data,
    ).model_dump()
