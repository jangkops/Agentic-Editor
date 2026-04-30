"""
Test: Bedrock Gateway
AWS Bedrock 연동 및 토큰 관리 테스트
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta

# Bedrock gateway 임포트 (경로 조정 필요)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ai_engine"))


class TestBedrockGateway:
    """Bedrock Gateway 통합 테스트"""

    @pytest.mark.unit
    def test_bedrock_gateway_initialization(self):
        """BedrockGateway 초기화 테스트"""
        try:
            from bedrock_gateway import BedrockGateway

            gateway = BedrockGateway(
                region="us-east-1",
                bedrock_user="test-user",
                bedrock_password="test-pass",
            )
            assert gateway is not None
            assert gateway.region == "us-east-1"
        except ImportError:
            pytest.skip("BedrockGateway not found")

    @pytest.mark.unit
    @patch("bedrock_gateway.boto3.client")
    def test_assume_role_success(self, mock_boto_client, mock_aws_credentials):
        """assume_role 성공 테스트"""
        try:
            from bedrock_gateway import BedrockGateway

            # Mock STS client
            mock_sts = MagicMock()
            mock_sts.assume_role.return_value = {
                "Credentials": {
                    "AccessKeyId": mock_aws_credentials["accessKeyId"],
                    "SecretAccessKey": mock_aws_credentials["secretAccessKey"],
                    "SessionToken": mock_aws_credentials["sessionToken"],
                    "Expiration": datetime.now() + timedelta(hours=1),
                }
            }
            mock_boto_client.return_value = mock_sts

            gateway = BedrockGateway(
                region="us-east-1",
                bedrock_user="test-user",
                bedrock_password="test-pass",
            )

            # assume_role 호출
            result = gateway.assume_role("test-role", "arn:aws:iam::123456789:role/test")
            assert result is not None

        except ImportError:
            pytest.skip("BedrockGateway not found")

    @pytest.mark.unit
    def test_token_expiry_tracking(self):
        """토큰 만료 시간 추적 테스트"""
        try:
            from bedrock_gateway import BedrockGateway

            gateway = BedrockGateway(
                region="us-east-1",
                bedrock_user="test-user",
                bedrock_password="test-pass",
            )

            # 만료 시간 설정
            expiry = datetime.now() + timedelta(hours=1)
            gateway.token_expiry = expiry

            # 만료되지 않았는지 확인
            assert gateway.is_token_expired() is False

            # 만료된 토큰 설정
            gateway.token_expiry = datetime.now() - timedelta(hours=1)
            assert gateway.is_token_expired() is True

        except ImportError:
            pytest.skip("BedrockGateway not found")

    @pytest.mark.unit
    @patch("bedrock_gateway.boto3.client")
    def test_bedrock_invoke_success(self, mock_boto_client, mock_bedrock_response):
        """Bedrock 모델 호출 성공 테스트"""
        try:
            from bedrock_gateway import BedrockGateway

            mock_bedrock = MagicMock()
            mock_bedrock.invoke_model.return_value = {
                "body": MagicMock(read=lambda: b'{"content": [{"text": "test"}]}')
            }
            mock_boto_client.return_value = mock_bedrock

            gateway = BedrockGateway(
                region="us-east-1",
                bedrock_user="test-user",
                bedrock_password="test-pass",
            )

            # 모델 호출
            result = gateway.invoke_model(
                model_id="anthropic.claude-v2",
                prompt="Hello",
            )
            assert result is not None

        except ImportError:
            pytest.skip("BedrockGateway not found")

    @pytest.mark.unit
    def test_concurrent_gateway_access(self):
        """동시 게이트웨이 접근 테스트"""
        try:
            from bedrock_gateway import BedrockGateway
            import threading

            gateways = []

            def create_gateway():
                gw = BedrockGateway(
                    region="us-east-1",
                    bedrock_user="test-user",
                    bedrock_password="test-pass",
                )
                gateways.append(gw)

            # 5개 스레드에서 동시 접근
            threads = [threading.Thread(target=create_gateway) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(gateways) == 5

        except ImportError:
            pytest.skip("BedrockGateway not found")

    @pytest.mark.integration
    def test_error_handling_invalid_credentials(self):
        """잘못된 자격증명 에러 처리 테스트"""
        try:
            from bedrock_gateway import BedrockGateway

            with pytest.raises(Exception):
                gateway = BedrockGateway(
                    region="us-east-1",
                    bedrock_user="invalid-user",
                    bedrock_password="invalid-pass",
                )
                # assume_role 호출 시도 (실제 AWS 호출)
                gateway.assume_role("test-role", "arn:aws:iam::123456789:role/test")

        except ImportError:
            pytest.skip("BedrockGateway not found")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
