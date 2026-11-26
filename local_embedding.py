"""在本机用中文 BGE 模型生成检索向量，资料不会发送到外部接口。"""

import asyncio
from pathlib import Path
import threading
import time
from typing import Literal

import numpy as np
from pydantic import Field

from agentscope.credential import CredentialBase
from agentscope.embedding import EmbeddingModelBase, EmbeddingModelCard, EmbeddingResponse, EmbeddingUsage


MODEL = "bge-small-zh-v1.5-local"
MODEL_DIRECTORY = Path(__file__).resolve().parent / ".runtime/models/bge-small-zh-v1.5"
_lock = threading.Lock()
_session = None
_tokenizer = None


def encode_texts(texts):
    """多个知识库共用一份 CPU 模型，并限制线程数量。"""
    global _session, _tokenizer
    import onnxruntime as ort
    from tokenizers import Tokenizer

    with _lock:
        if _session is None:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            options.inter_op_num_threads = 1
            _session = ort.InferenceSession(str(MODEL_DIRECTORY / "onnx/model_quantized.onnx"),
                                            sess_options=options, providers=["CPUExecutionProvider"])
            _tokenizer = Tokenizer.from_file(str(MODEL_DIRECTORY / "tokenizer.json"))
            _tokenizer.enable_truncation(max_length=512)
            _tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        encoded = _tokenizer.encode_batch(texts)
        arrays = {"input_ids": np.array([item.ids for item in encoded], dtype=np.int64),
                  "attention_mask": np.array([item.attention_mask for item in encoded], dtype=np.int64),
                  "token_type_ids": np.array([item.type_ids for item in encoded], dtype=np.int64)}
        inputs = {item.name: arrays[item.name] for item in _session.get_inputs()}
        vectors = _session.run(None, inputs)[0][:, 0, :].astype(np.float32)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        return vectors.tolist(), sum(sum(item.attention_mask) for item in encoded)


class LocalEmbeddingCredential(CredentialBase):
    """仅供本地检索使用的凭据，不包含密码或 API key。"""

    type: Literal["local_embedding_credential"] = "local_embedding_credential"
    name: str = Field(default="本地中文知识检索（无需密钥）", description="检索凭据名称")

    @classmethod
    def get_chat_model_classes(cls):
        return []

    @classmethod
    def get_embedding_model_class(cls):
        return LocalChineseEmbedding


class LocalChineseEmbedding(EmbeddingModelBase[str]):
    """用于原版知识库的本地中文向量适配器。"""

    def __init__(self, credential, model=MODEL, dimensions=512, parameters=None, context_size=512, **kwargs):
        if model != MODEL or dimensions != 512:
            raise ValueError("本地中文检索模型固定使用 512 维，不能更换名称或维度")
        super().__init__(credential=credential, model=model, dimensions=512, parameters=parameters,
                         context_size=context_size, batch_size=16, max_retries=0, retry_delay=0)

    @classmethod
    def list_models(cls, custom_yaml_dir=None):
        return [EmbeddingModelCard(name=MODEL, label="本地中文 BGE（无需 API key）", dimensions=512,
                                   context_size=512, parameter_schema=cls.Parameters.model_json_schema())]

    async def _call_api(self, inputs, **kwargs):
        started = time.monotonic()
        vectors, tokens = await asyncio.to_thread(encode_texts, inputs)
        return EmbeddingResponse(embeddings=vectors, usage=EmbeddingUsage(tokens=tokens, time=time.monotonic() - started))
