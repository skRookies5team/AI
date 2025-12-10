"""Setup script for CLOVA Speech STT SDK"""

from setuptools import setup, find_packages

setup(
    name="ncp-clova-speech",
    version="1.0.0",
    description="NAVER Cloud Platform CLOVA Speech STT SDK",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "requests>=2.31.0",
        "python-dotenv>=1.0.0",
        "fastapi>=0.104.0",
        "uvicorn[standard]>=0.24.0",
        "pydantic>=2.4.0",
        "openai>=1.3.0",
    ],
    python_requires=">=3.8",
)