"""Google OAuth 토큰 암호화/복호화 서비스."""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet


class TokenEncryptionService:
    """토큰 암호화/복호화 관리.
    
    Google OAuth refresh_token을 DB에 안전하게 저장하기 위한 암호화 서비스.
    환경 변수 TOKEN_ENCRYPTION_KEY를 사용하여 암호화/복호화를 수행합니다.
    
    Examples:
        >>> service = TokenEncryptionService()
        >>> encrypted = service.encrypt("my_refresh_token")
        >>> decrypted = service.decrypt(encrypted)
        >>> assert decrypted == "my_refresh_token"
    """
    
    def __init__(self) -> None:
        """암호화 키 초기화.
        
        Raises:
            ValueError: TOKEN_ENCRYPTION_KEY가 설정되지 않은 경우
        """
        encryption_key = os.getenv("TOKEN_ENCRYPTION_KEY")
        
        if not encryption_key:
            # 개발 환경용: 자동 생성 (프로덕션에서는 반드시 .env에 설정)
            print("⚠️ TOKEN_ENCRYPTION_KEY가 설정되지 않았습니다. 자동 생성된 키를 사용합니다.")
            print("⚠️ 프로덕션 환경에서는 반드시 .env에 고정 키를 설정하세요!")
            generated_key = Fernet.generate_key().decode()
            print(f"⚠️ 생성된 키: {generated_key}")
            print(f"⚠️ 이 키를 .env 파일에 추가하세요: TOKEN_ENCRYPTION_KEY={generated_key}")
            encryption_key = generated_key
        
        self.cipher = Fernet(
            encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        )
    
    def encrypt(self, plaintext: str) -> str:
        """평문을 암호화.
        
        Args:
            plaintext: 암호화할 평문 (refresh_token 등)
            
        Returns:
            Base64 인코딩된 암호문
            
        Raises:
            ValueError: plaintext가 비어있는 경우
        """
        if not plaintext:
            raise ValueError("암호화할 텍스트가 비어있습니다.")
        
        encrypted = self.cipher.encrypt(plaintext.encode())
        return encrypted.decode()
    
    def decrypt(self, ciphertext: str) -> Optional[str]:
        """암호문을 복호화.
        
        Args:
            ciphertext: Base64 인코딩된 암호문
            
        Returns:
            복호화된 평문 또는 None (실패 시)
        """
        if not ciphertext:
            return None
        
        try:
            decrypted = self.cipher.decrypt(ciphertext.encode())
            return decrypted.decode()
        except Exception as e:
            print(f"❌ 토큰 복호화 실패: {e}")
            return None


# 싱글톤 인스턴스
_encryption_service: Optional[TokenEncryptionService] = None


def get_encryption_service() -> TokenEncryptionService:
    """암호화 서비스 싱글톤 인스턴스 반환.
    
    Returns:
        TokenEncryptionService 인스턴스
    """
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = TokenEncryptionService()
    return _encryption_service
