"""
审计日志系统 - 记录用户交互和系统事件
像行车记录仪一样保存重要信息，用于调试和审计
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AuditLogger:
    """审计日志记录器 - 保存用户交互和系统事件"""
    
    def __init__(
        self,
        log_dir: str = "logs",
        retention_days: int = 7,
        log_http: bool = False,
    ):
        """
        初始化审计日志系统
        
        Args:
            log_dir: 日志目录
            retention_days: 日志保留天数（默认7天）
            log_http: 是否记录正常的HTTP请求（默认False，避免噪音）
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.retention_days = retention_days
        self.log_http = log_http
        self._logger = logging.getLogger(f"{__name__}.AuditLogger")
        
        # 清理旧日志
        self._cleanup_old_logs()
    
    def _get_log_file_path(self, log_type: str, date: Optional[datetime] = None) -> Path:
        """获取日志文件路径"""
        if date is None:
            date = datetime.now()
        date_str = date.strftime("%Y-%m-%d")
        filename = f"{log_type}_{date_str}.jsonl"
        return self.log_dir / filename
    
    def _cleanup_old_logs(self) -> None:
        """清理超过保留期的日志文件"""
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        
        for log_file in self.log_dir.glob("*.jsonl"):
            try:
                # 从文件名提取日期
                parts = log_file.stem.split("_")
                if len(parts) >= 2:
                    date_str = parts[-1]  # 最后一部分是日期
                    file_date = datetime.strptime(date_str, "%Y-%m-%d")
                    if file_date < cutoff_date:
                        log_file.unlink()
                        self._logger.info(f"Deleted old log file: {log_file.name}")
            except Exception as e:
                self._logger.warning(f"Failed to check/delete log file {log_file.name}: {e}")
    
    def _write_log(self, log_type: str, data: Dict[str, Any]) -> None:
        """写入日志到文件"""
        try:
            log_file = self._get_log_file_path(log_type)
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                **data,
            }
            
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to write audit log: {e}")
    
    def log_user_interaction(
        self,
        user_id: str,
        username: Optional[str],
        input_text: str,
        output_text: str,
        success: bool,
        source: str = "telegram",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        记录用户交互（输入/输出）
        
        Args:
            user_id: 用户ID
            username: 用户名（可选）
            input_text: 用户输入
            output_text: 系统输出
            success: 是否成功
            source: 来源（telegram/email等）
            metadata: 额外元数据（事件列表、链接等）
        """
        data = {
            "type": "user_interaction",
            "user_id": str(user_id),
            "username": username,
            "source": source,
            "input": input_text[:1000],  # 限制长度
            "output": output_text[:2000],  # 限制长度
            "success": success,
        }
        
        if metadata:
            data["metadata"] = metadata
        
        self._write_log("interactions", data)
    
    def log_error(
        self,
        error_type: str,
        error_message: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        traceback: Optional[str] = None,
    ) -> None:
        """
        记录错误和异常
        
        Args:
            error_type: 错误类型（Exception类名）
            error_message: 错误消息
            user_id: 用户ID（如果有）
            username: 用户名（如果有）
            context: 上下文信息
            traceback: 堆栈跟踪（可选）
        """
        data = {
            "type": "error",
            "error_type": error_type,
            "error_message": error_message,
        }
        
        if user_id:
            data["user_id"] = str(user_id)
        if username:
            data["username"] = username
        if context:
            data["context"] = context
        if traceback:
            data["traceback"] = traceback[:5000]  # 限制长度
        
        self._write_log("errors", data)
    
    def log_system_event(
        self,
        event_type: str,
        description: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        记录系统事件（重要操作）
        
        Args:
            event_type: 事件类型（如 "auth_success", "calendar_sync", "model_change"）
            description: 事件描述
            metadata: 额外元数据
        """
        data = {
            "type": "system_event",
            "event_type": event_type,
            "description": description,
        }
        
        if metadata:
            data["metadata"] = metadata
        
        self._write_log("events", data)
    
    def log_api_call(
        self,
        api_name: str,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
    ) -> None:
        """
        记录API调用（仅记录重要或失败的调用）
        
        Args:
            api_name: API名称（如 "openai_completion", "google_calendar_create"）
            request_data: 请求数据（可选，避免记录敏感信息）
            response_data: 响应数据（可选）
            error: 错误信息（如果有）
            duration_ms: 耗时（毫秒）
        """
        # 只在有错误或明确需要记录时才记录
        if not self.log_http and not error:
            return
        
        data = {
            "type": "api_call",
            "api_name": api_name,
        }
        
        if request_data:
            # 清理敏感信息
            safe_request = self._sanitize_data(request_data)
            data["request"] = safe_request
        
        if response_data:
            data["response"] = response_data
        
        if error:
            data["error"] = error
        
        if duration_ms:
            data["duration_ms"] = duration_ms
        
        self._write_log("api_calls", data)
    
    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """清理敏感信息（如API密钥）"""
        sensitive_keys = {"api_key", "token", "password", "secret", "credentials"}
        sanitized = {}
        
        for key, value in data.items():
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_data(value)
            else:
                sanitized[key] = value
        
        return sanitized
    
    def log_api_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        """
        记录 API 使用量（token 统计）
        
        Args:
            model: 模型名称
            prompt_tokens: prompt tokens
            completion_tokens: completion tokens
            total_tokens: 总 tokens
        """
        data = {
            "type": "api_usage",
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        
        self._write_log("api_usage", data)
    
    def query_logs(
        self,
        log_type: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询日志（用于调试）
        
        Args:
            log_type: 日志类型（interactions/errors/events/api_calls）
            start_date: 开始日期
            end_date: 结束日期
            limit: 返回条数限制
        
        Returns:
            日志条目列表
        """
        results = []
        
        if start_date is None:
            start_date = datetime.now() - timedelta(days=self.retention_days)
        if end_date is None:
            end_date = datetime.now()
        
        current_date = start_date
        while current_date <= end_date and len(results) < limit:
            log_file = self._get_log_file_path(log_type, current_date)
            if log_file.exists():
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if len(results) >= limit:
                                break
                            try:
                                entry = json.loads(line.strip())
                                entry_date = datetime.fromisoformat(entry["timestamp"])
                                if start_date <= entry_date <= end_date:
                                    results.append(entry)
                            except Exception:
                                continue
                except Exception as e:
                    self._logger.warning(f"Failed to read log file {log_file}: {e}")
            
            current_date += timedelta(days=1)
        
        # 按时间倒序排列
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return results[:limit]

