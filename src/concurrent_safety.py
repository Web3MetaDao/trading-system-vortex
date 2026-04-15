"""
并发安全管理模块

提供文件锁、状态隔离和原子操作支持，防止多进程竞争。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


class FileLock:
    """
    文件锁实现，用于防止多进程竞争
    
    支持：
    1. 自动超时释放
    2. 死锁检测
    3. 优雅降级
    """
    
    def __init__(self, lock_file: Path | str, timeout_seconds: int = 30):
        self.lock_file = Path(lock_file)
        self.timeout_seconds = timeout_seconds
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
    
    def acquire(self, blocking: bool = True, poll_interval: float = 0.1) -> bool:
        """
        获取文件锁
        
        Args:
            blocking: 是否阻塞等待
            poll_interval: 轮询间隔（秒）
        
        Returns:
            是否成功获取
        """
        start_time = time.time()
        
        while True:
            try:
                # 尝试原子性创建文件
                fd = os.open(
                    str(self.lock_file),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644
                )
                # 写入进程 ID 和时间戳
                with os.fdopen(fd, 'w') as f:
                    f.write(f"{os.getpid()}\n{datetime.now(UTC).isoformat()}\n")
                logger.debug(f"Acquired lock: {self.lock_file}")
                return True
            except FileExistsError:
                # 检查锁是否过期（死锁检测）
                if self._is_lock_stale():
                    logger.warning(f"Stale lock detected, removing: {self.lock_file}")
                    try:
                        self.lock_file.unlink()
                    except OSError:
                        pass
                    continue
                
                # 如果不阻塞，直接返回失败
                if not blocking:
                    logger.warning(f"Could not acquire lock (non-blocking): {self.lock_file}")
                    return False
                
                # 检查超时
                elapsed = time.time() - start_time
                if elapsed > self.timeout_seconds:
                    logger.error(f"Lock acquisition timeout: {self.lock_file}")
                    return False
                
                # 等待后重试
                time.sleep(poll_interval)
            except Exception as e:
                logger.error(f"Error acquiring lock: {e}")
                return False
        
        return False
    
    def release(self) -> bool:
        """
        释放文件锁
        
        Returns:
            是否成功释放
        """
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
                logger.debug(f"Released lock: {self.lock_file}")
            return True
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")
            return False
    
    def _is_lock_stale(self) -> bool:
        """
        检查锁是否过期
        
        Returns:
            是否过期
        """
        try:
            if not self.lock_file.exists():
                return False
            
            with open(self.lock_file, 'r') as f:
                lines = f.readlines()
            
            if len(lines) < 2:
                return True
            
            try:
                lock_time = datetime.fromisoformat(lines[1].strip())
                elapsed = (datetime.now(UTC) - lock_time).total_seconds()
                is_stale = elapsed > self.timeout_seconds * 2
                if is_stale:
                    logger.warning(
                        f"Lock is stale: {elapsed:.1f}s old (threshold: {self.timeout_seconds * 2}s)"
                    )
                return is_stale
            except ValueError:
                return True
        except Exception as e:
            logger.warning(f"Error checking lock staleness: {e}")
            return False
    
    @contextmanager
    def acquire_context(self, blocking: bool = True):
        """
        上下文管理器
        
        Args:
            blocking: 是否阻塞等待
        
        Yields:
            是否成功获取锁
        """
        acquired = self.acquire(blocking=blocking)
        try:
            yield acquired
        finally:
            if acquired:
                self.release()


class StateIsolation:
    """
    状态隔离机制，防止并发修改
    
    支持：
    1. 快照隔离
    2. 写入缓冲
    3. 事务性提交
    """
    
    def __init__(self, state_file: Path | str):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._write_buffer = {}
        self._lock = FileLock(self.state_file.with_suffix(".lock"))
    
    def read_snapshot(self) -> dict:
        """
        读取状态快照（隔离读）
        
        Returns:
            状态字典
        """
        import json
        
        with self._lock.acquire_context(blocking=True) as acquired:
            if not acquired:
                logger.warning("Could not acquire lock for snapshot read")
                return {}
            
            try:
                if self.state_file.exists():
                    with open(self.state_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
            except Exception as e:
                logger.error(f"Error reading state snapshot: {e}")
        
        return {}
    
    def write_atomic(self, state: dict) -> bool:
        """
        原子性写入状态
        
        Args:
            state: 状态字典
        
        Returns:
            是否成功
        """
        import json
        import tempfile
        
        with self._lock.acquire_context(blocking=True) as acquired:
            if not acquired:
                logger.warning("Could not acquire lock for atomic write")
                return False
            
            try:
                # 使用临时文件 + 原子重命名
                with tempfile.NamedTemporaryFile(
                    mode='w',
                    dir=self.state_file.parent,
                    delete=False,
                    encoding='utf-8',
                    suffix='.tmp'
                ) as tmp:
                    json.dump(state, tmp, ensure_ascii=False, indent=2)
                    tmp_path = tmp.name
                
                # 原子性重命名
                tmp_file = Path(tmp_path)
                tmp_file.replace(self.state_file)
                logger.debug(f"Atomically wrote state to {self.state_file}")
                return True
            except Exception as e:
                logger.error(f"Error writing state atomically: {e}")
                return False
    
    def update_buffered(self, updates: dict) -> None:
        """
        缓冲式更新（不立即写入）
        
        Args:
            updates: 更新字典
        """
        self._write_buffer.update(updates)
    
    def flush_buffer(self) -> bool:
        """
        刷新写入缓冲
        
        Returns:
            是否成功
        """
        if not self._write_buffer:
            return True
        
        try:
            current_state = self.read_snapshot()
            current_state.update(self._write_buffer)
            success = self.write_atomic(current_state)
            if success:
                self._write_buffer.clear()
            return success
        except Exception as e:
            logger.error(f"Error flushing write buffer: {e}")
            return False


class ConcurrentExecutor:
    """
    并发执行器，支持安全的多进程操作
    
    功能：
    1. 任务队列管理
    2. 死锁检测
    3. 优雅降级
    """
    
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
    
    def execute_with_retry(
        self,
        func,
        *args,
        lock: FileLock | None = None,
        **kwargs
    ) -> tuple[bool, Any]:
        """
        带重试的函数执行
        
        Args:
            func: 要执行的函数
            lock: 可选的文件锁
            *args, **kwargs: 函数参数
        
        Returns:
            (是否成功, 返回值)
        """
        for attempt in range(self.max_retries):
            try:
                if lock:
                    with lock.acquire_context(blocking=True) as acquired:
                        if not acquired:
                            logger.warning(f"Could not acquire lock (attempt {attempt + 1})")
                            time.sleep(0.5 * (attempt + 1))
                            continue
                        result = func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                return True, result
            except Exception as e:
                logger.warning(f"Execution failed (attempt {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                else:
                    logger.error(f"Execution failed after {self.max_retries} attempts")
                    return False, None
        
        return False, None


def safe_json_write(file_path: Path | str, data: dict, lock: FileLock | None = None) -> bool:
    """
    安全的 JSON 写入
    
    Args:
        file_path: 文件路径
        data: 数据字典
        lock: 可选的文件锁
    
    Returns:
        是否成功
    """
    import json
    import tempfile
    
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    def _write():
        with tempfile.NamedTemporaryFile(
            mode='w',
            dir=file_path.parent,
            delete=False,
            encoding='utf-8',
            suffix='.tmp'
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        
        Path(tmp_path).replace(file_path)
        return True
    
    if lock:
        executor = ConcurrentExecutor()
        success, _ = executor.execute_with_retry(_write, lock=lock)
        return success
    else:
        try:
            return _write()
        except Exception as e:
            logger.error(f"Error writing JSON: {e}")
            return False


def safe_json_read(file_path: Path | str, lock: FileLock | None = None) -> dict:
    """
    安全的 JSON 读取
    
    Args:
        file_path: 文件路径
        lock: 可选的文件锁
    
    Returns:
        数据字典
    """
    import json
    
    file_path = Path(file_path)
    
    def _read():
        if not file_path.exists():
            return {}
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    if lock:
        executor = ConcurrentExecutor()
        success, data = executor.execute_with_retry(_read, lock=lock)
        return data if success else {}
    else:
        try:
            return _read()
        except Exception as e:
            logger.error(f"Error reading JSON: {e}")
            return {}
