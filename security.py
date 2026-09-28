"""安全模块 - JWT认证和密码加密

提供 JWT Token 生成/验证、密码加密/验证、敏感信息加密存储等功能
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import secrets
import hashlib

from jose import JWTError, jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet


# ==================== JWT Token ====================

# 生产环境应该从环境变量读取
SECRET_KEY = secrets.token_urlsafe(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72


class JWTAuth:
    """JWT 认证工具类"""

    def __init__(self, secret_key: str = None, algorithm: str = None):
        self.secret_key = secret_key or SECRET_KEY
        self.algorithm = algorithm or ALGORITHM

    def create_access_token(
        self,
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None
    ) -> str:
        """创建访问令牌"""
        to_encode = data.copy()

        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)

        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),  # issued at
            "jti": secrets.token_urlsafe(16)  # JWT ID
        })

        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """验证令牌"""
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm]
            )
            return payload
        except JWTError as e:
            print(f"JWT验证失败: {e}")
            return None

    def decode_token(self, token: str, verify: bool = True) -> Optional[Dict[str, Any]]:
        """解码令牌（可选择是否验证）"""
        try:
            if verify:
                return self.verify_token(token)
            else:
                return jwt.decode(
                    token,
                    self.secret_key,
                    algorithms=[self.algorithm],
                    options={"verify_signature": False}
                )
        except JWTError:
            return None

    def get_user_from_token(self, token: str) -> Optional[str]:
        """从令牌中获取用户信息"""
        payload = self.verify_token(token)
        if payload:
            return payload.get("sub")  # subject (用户名或ID)
        return None


# ==================== 密码加密 ====================

# 使用 bcrypt，rounds=12（安全性和性能平衡）
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12
)


class PasswordManager:
    """密码管理工具类"""

    @staticmethod
    def hash_password(password: str) -> str:
        """加密密码"""
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception as e:
            print(f"密码验证错误: {e}")
            return False

    @staticmethod
    def needs_update(hashed_password: str) -> bool:
        """检查密码哈希是否需要更新"""
        return pwd_context.needs_update(hashed_password)

    @staticmethod
    def validate_password_strength(password: str) -> tuple[bool, str]:
        """验证密码强度"""
        if len(password) < 8:
            return False, "密码长度至少为8位"

        if password.isdigit():
            return False, "密码不能为纯数字"

        if password.isalpha():
            return False, "密码不能为纯字母"

        # 检查常见弱密码
        weak_passwords = [
            'password', '12345678', 'admin123', 'qwerty123',
            'abc12345', '11111111', '88888888'
        ]
        if password.lower() in weak_passwords:
            return False, "密码过于简单，请使用更复杂的密码"

        return True, "密码强度合格"


# ==================== 敏感信息加密 ====================

class SecureStorage:
    """敏感信息加密存储"""

    def __init__(self, key: Optional[bytes] = None):
        """
        初始化加密存储
        key: 加密密钥（32字节），如果不提供则生成新的
        """
        if key is None:
            key = Fernet.generate_key()
        self.cipher = Fernet(key)
        self.key = key

    @classmethod
    def from_password(cls, password: str, salt: bytes = None):
        """从密码生成密钥"""
        if salt is None:
            salt = b'spark_salt_v1'  # 生产环境应该随机生成并持久化

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return cls(key)

    def encrypt(self, data: str) -> str:
        """加密字符串"""
        encrypted = self.cipher.encrypt(data.encode())
        return encrypted.decode()

    def decrypt(self, encrypted: str) -> str:
        """解密字符串"""
        try:
            decrypted = self.cipher.decrypt(encrypted.encode())
            return decrypted.decode()
        except Exception as e:
            print(f"解密失败: {e}")
            return None

    def encrypt_dict(self, data: dict) -> str:
        """加密字典（转JSON）"""
        import json
        json_str = json.dumps(data, ensure_ascii=False)
        return self.encrypt(json_str)

    def decrypt_dict(self, encrypted: str) -> Optional[dict]:
        """解密字典"""
        import json
        decrypted = self.decrypt(encrypted)
        if decrypted:
            try:
                return json.loads(decrypted)
            except json.JSONDecodeError:
                return None
        return None

    def get_key_string(self) -> str:
        """获取密钥字符串（用于持久化）"""
        return self.key.decode()


# ==================== 便捷函数 ====================

# 全局实例
_jwt_auth = JWTAuth()
_password_manager = PasswordManager()


def create_token(username: str, expires_hours: int = None) -> str:
    """创建JWT令牌"""
    data = {"sub": username}
    if expires_hours:
        expires_delta = timedelta(hours=expires_hours)
        return _jwt_auth.create_access_token(data, expires_delta)
    return _jwt_auth.create_access_token(data)


def verify_token(token: str) -> Optional[str]:
    """验证令牌并返回用户名"""
    return _jwt_auth.get_user_from_token(token)


def hash_password(password: str) -> str:
    """加密密码"""
    return _password_manager.hash_password(password)


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return _password_manager.verify_password(plain, hashed)


def validate_password(password: str) -> tuple[bool, str]:
    """验证密码强度"""
    return _password_manager.validate_password_strength(password)


# ==================== Cookie加密存储 ====================

def encrypt_cookie(cookie_data: str, password: str = None) -> str:
    """加密Cookie数据"""
    if password is None:
        password = SECRET_KEY

    storage = SecureStorage.from_password(password)
    return storage.encrypt(cookie_data)


def decrypt_cookie(encrypted_data: str, password: str = None) -> Optional[str]:
    """解密Cookie数据"""
    if password is None:
        password = SECRET_KEY

    storage = SecureStorage.from_password(password)
    return storage.decrypt(encrypted_data)


# ==================== API密钥生成 ====================

def generate_api_key(length: int = 32) -> str:
    """生成API密钥"""
    return secrets.token_urlsafe(length)


def hash_api_key(api_key: str) -> str:
    """哈希API密钥（用于存储）"""
    return hashlib.sha256(api_key.encode()).hexdigest()


# 使用示例
if __name__ == '__main__':
    print("=== JWT Token 测试 ===")
    token = create_token("admin", expires_hours=1)
    print(f"Token: {token[:50]}...")

    username = verify_token(token)
    print(f"验证结果: {username}")

    print("\n=== 密码加密测试 ===")
    password = "MySecurePass123"
    hashed = hash_password(password)
    print(f"原密码: {password}")
    print(f"哈希值: {hashed}")
    print(f"验证: {verify_password(password, hashed)}")

    is_valid, msg = validate_password(password)
    print(f"密码强度: {msg}")

    print("\n=== Cookie加密测试 ===")
    cookie = "sessionid=abc123; sid_guard=xyz789"
    encrypted = encrypt_cookie(cookie)
    print(f"原始Cookie: {cookie}")
    print(f"加密后: {encrypted[:50]}...")

    decrypted = decrypt_cookie(encrypted)
    print(f"解密后: {decrypted}")
    print(f"匹配: {cookie == decrypted}")

    print("\n=== API密钥生成 ===")
    api_key = generate_api_key()
    print(f"API Key: {api_key}")
    print(f"Hash: {hash_api_key(api_key)}")
