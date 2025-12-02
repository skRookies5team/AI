"""Redis 기반 체크포인터 설정 유틸리티."""

from __future__ import annotations

import os
from typing import Optional

# 모듈 레벨 변수
REDIS_IMPORT_ERROR: Optional[str] = None
RedisSaver = None
Redis = None

# Redis checkpoint saver import 시도 (비동기 버전 사용)
# 참고: LangGraph의 redis checkpoint는 AsyncRedisSaver만 제공하며,
# RedisStore는 별도로 존재하지 않습니다. AsyncRedisSaver가 내부적으로 Redis를 사용합니다.
try:
    from langgraph.checkpoint.redis import AsyncRedisSaver
    from redis import Redis
    REDIS_AVAILABLE = True
    # 타입 힌트를 위해 별칭 사용
    RedisSaver = AsyncRedisSaver
except ImportError as e:
    REDIS_AVAILABLE = False
    REDIS_IMPORT_ERROR = str(e)


def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    try:
        env_path = os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    # 빈 문자열은 None으로 처리하지 않고 그대로 저장 (환경 변수는 빈 문자열도 유효할 수 있음)
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception as e:
        print(f"[WARNING] .env 파일 로드 실패: {e}")


_load_env_file()


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 안전 조회 (공백 제거 및 빈 문자열 처리).
    
    Args:
        name: 환경 변수 이름
        default: 기본값 (환경 변수가 없거나 빈 문자열인 경우)
        
    Returns:
        환경 변수 값 또는 기본값
    """
    value = os.environ.get(name)
    if value is None:
        return default
    # 공백 제거
    value = value.strip()
    # 빈 문자열인 경우 기본값 반환
    if not value:
        return default
    return value


def get_redis_checkpointer() -> Optional["RedisSaver"]:
    """Redis 체크포인터 인스턴스를 생성합니다.
    
    RedisStore와 RedisSaver를 사용하여 상태를 Redis에 저장합니다.
    
    환경 변수:
        HOST: Redis 호스트 (기본값: localhost)
        REDIS_PORT: Redis 포트 (기본값: 6379)
        REDIS_PASSWORD: Redis 비밀번호 (선택적)
        REDIS_DB: Redis DB 번호 (기본값: 0)
    
    Returns:
        RedisSaver 인스턴스 (RedisStore를 내부적으로 사용), Redis를 사용할 수 없는 경우 None
    """
    if not REDIS_AVAILABLE:
        error_msg = REDIS_IMPORT_ERROR or '알 수 없는 오류'
        print("[WARNING] Redis 체크포인터를 사용할 수 없습니다. (패키지 미설치)")
        print(f"   Import 오류: {error_msg}")
        print("   설치 명령: pip install redis langgraph-checkpoint-redis")
        print("   또는: uv pip install redis langgraph-checkpoint-redis")
        return None
    
    try:
        # 환경 변수 읽기 (공백 제거 및 기본값 처리)
        # REDIS_HOST 우선 시도, 없으면 HOST, 없으면 localhost
        redis_host = _get_env("HOST")
        redis_port_str = _get_env("REDIS_PORT")
        redis_password = _get_env("REDIS_PASSWORD")  # None이면 비밀번호 없음
        redis_db_str = _get_env("REDIS_DB") or "0"
        
        # None 체크 및 기본값 설정
        if not redis_host:
            redis_host = "localhost"
            print("[WARNING] Redis 호스트가 설정되지 않았습니다. 기본값 localhost 사용")
        
        # 포트와 DB 번호를 정수로 변환
        try:
            redis_port = int(redis_port_str.strip() if redis_port_str else "6379")
        except (ValueError, AttributeError) as e:
            print(f"[WARNING] REDIS_PORT 값이 유효하지 않습니다: '{redis_port_str}'. 기본값 6379 사용")
            redis_port = 6379
        
        try:
            redis_db = int(redis_db_str.strip() if redis_db_str else "0")
        except (ValueError, AttributeError) as e:
            print(f"[WARNING] REDIS_DB 값이 유효하지 않습니다: '{redis_db_str}'. 기본값 0 사용")
            redis_db = 0
        
        # 빈 문자열인 경우 None으로 처리
        if redis_password == "":
            redis_password = None
        
        # 연결 정보 출력 (비밀번호는 제외)
        print(f"[INFO] Redis 연결 시도: host={redis_host}, port={redis_port}, db={redis_db}")
        
        # Redis URL 구성
        if redis_password:
            redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}"
        else:
            redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
        
        # Redis 연결 테스트 (선택적, 동기 클라이언트 사용)
        try:
            test_client = Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                db=redis_db,
                decode_responses=True,
                socket_connect_timeout=5,  # 연결 타임아웃 5초
            )
            test_client.ping()
            test_client.close()
            print(f"[SUCCESS] Redis 연결 성공: {redis_host}:{redis_port}/{redis_db}")
        except Exception as test_error:
            print(f"[WARNING] Redis 연결 테스트 실패: {test_error}")
            print("  AsyncRedisSaver를 생성하지만, 실제 연결은 지연될 수 있습니다.")
        
        # AsyncRedisSaver는 이벤트 루프가 필요하므로 지연 초기화 래퍼 사용
        # LangGraph가 비동기 컨텍스트에서 실행될 때 초기화됨
        # AsyncRedisSaver는 내부적으로 RedisStore를 사용하여 상태를 저장합니다
        class LazyAsyncRedisSaver:
            """AsyncRedisSaver의 지연 초기화 래퍼.
            
            RedisStore와 RedisSaver를 사용하여 상태를 Redis에 저장합니다.
            """
            
            def __init__(self, redis_url: str, ttl: int = 3600):
                self._redis_url = redis_url
                self._ttl = ttl
                self._saver: Optional[RedisSaver] = None
                self._setup_done = False
                self._setup_lock = None  # asyncio.Lock은 나중에 초기화
                self._reconnect_attempts = 0
                self._max_reconnect_attempts = 3
            
            def _ensure_initialized(self, force_reconnect: bool = False):
                """실제 AsyncRedisSaver를 초기화 (이벤트 루프가 있을 때).
                
                Args:
                    force_reconnect: True이면 기존 연결을 무시하고 재생성
                """
                if self._saver is None or force_reconnect:
                    import asyncio
                    try:
                        # 실행 중인 이벤트 루프가 있어야 함
                        loop = asyncio.get_running_loop()
                        # 락 초기화
                        if self._setup_lock is None:
                            self._setup_lock = asyncio.Lock()
                        
                        # 기존 saver가 있으면 연결 해제 시도
                        if force_reconnect and self._saver is not None:
                            try:
                                # 내부 Redis 클라이언트가 있다면 닫기 시도
                                if hasattr(self._saver, '_redis') and self._saver._redis:
                                    # 비동기 클라이언트는 aclose() 사용
                                    if hasattr(self._saver._redis, 'aclose'):
                                        # aclose는 코루틴이므로 await 필요하지만 여기서는 동기 컨텍스트
                                        pass
                            except Exception:
                                pass
                            self._saver = None
                            self._setup_done = False
                        
                        # ttl을 딕셔너리 형식으로 전달 (AsyncRedisSaver의 시그니처: ttl: Optional[Dict[str, Any]])
                        ttl_dict = {"default_ttl": self._ttl} if self._ttl else None
                        if ttl_dict:
                            self._saver = RedisSaver(redis_url=self._redis_url, ttl=ttl_dict)
                        else:
                            self._saver = RedisSaver(redis_url=self._redis_url)
                        
                        # 재연결 성공 시 카운터 리셋
                        if force_reconnect:
                            self._reconnect_attempts = 0
                    except RuntimeError:
                        # 이벤트 루프가 없으면 나중에 다시 시도
                        raise RuntimeError(
                            "AsyncRedisSaver 초기화를 위해서는 실행 중인 이벤트 루프가 필요합니다. "
                            "LangGraph가 비동기 컨텍스트에서 실행될 때 자동으로 초기화됩니다."
                        )
                return self._saver
            
            async def _ensure_setup(self):
                """인덱스가 설정되었는지 확인하고, 없으면 설정."""
                if self._setup_done:
                    return
                
                import asyncio
                saver = self._ensure_initialized()
                
                # 락을 사용하여 동시 접근 방지
                if self._setup_lock is None:
                    loop = asyncio.get_running_loop()
                    self._setup_lock = asyncio.Lock()
                
                async with self._setup_lock:
                    # 다시 확인 (다른 코루틴이 이미 설정했을 수 있음)
                    if self._setup_done:
                        return
                    
                    try:
                        if hasattr(saver, 'setup') and asyncio.iscoroutinefunction(saver.setup):
                            await saver.setup()
                            self._setup_done = True
                            print("[INFO] ✅ Redis 인덱스 자동 생성 완료")
                    except Exception as e:
                        error_str = str(e).lower()
                        # 인덱스가 이미 존재하는 경우는 성공으로 간주
                        if "index already exists" in error_str or "already exist" in error_str:
                            self._setup_done = True
                            print("[INFO] ✅ Redis 인덱스 이미 존재함")
                        else:
                            print(f"[WARNING] Redis 인덱스 설정 중 오류: {e}")
                            # 에러가 발생해도 계속 진행 (다음 시도에서 재시도될 수 있음)
            
            def __getattr__(self, name: str):
                """모든 속성 접근을 실제 saver로 위임."""
                import asyncio
                saver = self._ensure_initialized()
                
                attr = getattr(saver, name)
                
                # 비동기 메서드인 경우 래핑하여 setup 보장
                if asyncio.iscoroutinefunction(attr):
                    async def wrapped_method(*args, **kwargs):
                        # 첫 번째 호출 전에 setup 확인 및 실행
                        await self._ensure_setup()
                        try:
                            return await attr(*args, **kwargs)
                        except Exception as e:
                            error_str = str(e).lower()
                            error_type = type(e).__name__
                            
                            # 연결 오류 감지
                            is_connection_error = (
                                "connection" in error_str or
                                "connectionreset" in error_str or
                                "network" in error_str or
                                "지정된 네트워크" in str(e) or
                                "error while reading" in error_str or
                                error_type in ["ConnectionError", "ConnectionResetError", "OSError"]
                            )
                            
                            # 연결 오류인 경우 재연결 시도
                            if is_connection_error and self._reconnect_attempts < self._max_reconnect_attempts:
                                self._reconnect_attempts += 1
                                print(f"[WARNING] Redis 연결 오류 감지 (시도 {self._reconnect_attempts}/{self._max_reconnect_attempts}): {e}")
                                print(f"[INFO] Redis 재연결 시도 중...")
                                
                                import asyncio
                                # 재연결 전 잠시 대기 (지수 백오프)
                                wait_time = min(0.5 * (2 ** (self._reconnect_attempts - 1)), 2.0)
                                await asyncio.sleep(wait_time)
                                
                                # 재연결 시도
                                try:
                                    self._ensure_initialized(force_reconnect=True)
                                    self._setup_done = False
                                    await self._ensure_setup()
                                    
                                    # 재시도
                                    return await attr(*args, **kwargs)
                                except Exception as reconnect_error:
                                    print(f"[ERROR] Redis 재연결 실패: {reconnect_error}")
                                    if self._reconnect_attempts >= self._max_reconnect_attempts:
                                        print(f"[ERROR] 최대 재연결 시도 횟수({self._max_reconnect_attempts}) 초과. 오류를 상위로 전파합니다.")
                                        raise
                                    raise
                            
                            # "No such index" 오류가 발생하면 setup을 다시 시도
                            if "no such index" in error_str and not self._setup_done:
                                print(f"[WARNING] 인덱스 오류 감지, setup 재시도: {e}")
                                self._setup_done = False
                                await self._ensure_setup()
                                # 재시도
                                return await attr(*args, **kwargs)
                            
                            # 최대 재시도 횟수 초과 시 오류 전파
                            if is_connection_error:
                                print(f"[ERROR] Redis 연결 오류: 최대 재시도 횟수 초과 또는 재연결 실패")
                            
                            raise
                    
                    return wrapped_method
                
                return attr
        
        # 지연 초기화 래퍼 반환
        checkpointer = LazyAsyncRedisSaver(redis_url=redis_url, ttl=3600)
        
        return checkpointer
        
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"[ERROR] Redis 연결 실패: {error_msg}")
        print(f"   호스트: {redis_host if 'redis_host' in locals() else 'N/A'}")
        print(f"   포트: {redis_port if 'redis_port' in locals() else 'N/A'}")
        print(f"   DB: {redis_db if 'redis_db' in locals() else 'N/A'}")
        print(f"   상세 오류: {traceback.format_exc()}")
        print("   체크포인터 없이 진행합니다.")
        return None

