"""Model loader with support for TensorRT and ONNX runtime."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class ModelLoader:
    """Loads detection models for edge inference.

    Supports TensorRT engines (.engine) and ONNX models (.onnx) with
    automatic fallback.
    """

    def __init__(self, model_path: str, device: str = "cuda:0"):
        self.model_path = model_path
        self.device = device
        self._engine = None
        self._runtime = None
        self._context = None
        self._backend = None

    def load(self) -> bool:
        """Load model, trying TensorRT first, then ONNX Runtime."""
        if not os.path.exists(self.model_path):
            logger.error("Model file not found: %s", self.model_path)
            return False

        ext = os.path.splitext(self.model_path)[1].lower()

        if ext == ".engine":
            return self._load_tensorrt()
        elif ext == ".onnx":
            return self._load_onnx()
        else:
            logger.error("Unsupported model format: %s", ext)
            return False

    def _load_tensorrt(self) -> bool:
        """Load a TensorRT engine."""
        try:
            import tensorrt as trt
            TRT_LOGGER = trt.Logger(trt.Logger.WARNING)
            runtime = trt.Runtime(TRT_LOGGER)

            with open(self.model_path, "rb") as f:
                engine_data = f.read()

            self._engine = runtime.deserialize_cuda_engine(engine_data)
            if self._engine is None:
                logger.error("Failed to deserialize TensorRT engine")
                return False

            self._context = self._engine.create_execution_context()
            self._runtime = runtime
            self._backend = "tensorrt"
            logger.info("TensorRT engine loaded: %s", self.model_path)
            return True

        except ImportError:
            logger.warning("TensorRT not available, trying ONNX Runtime")
            onnx_path = self.model_path.replace(".engine", ".onnx")
            if os.path.exists(onnx_path):
                self.model_path = onnx_path
                return self._load_onnx()
            return False
        except Exception as exc:
            logger.error("TensorRT load failed: %s", exc)
            return False

    def _load_onnx(self) -> bool:
        """Load an ONNX model via ONNX Runtime."""
        try:
            import onnxruntime as ort

            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            self._engine = ort.InferenceSession(self.model_path, providers=providers)
            self._backend = "onnxruntime"
            logger.info("ONNX Runtime model loaded: %s", self.model_path)
            return True
        except ImportError:
            logger.error("ONNX Runtime not available")
            return False
        except Exception as exc:
            logger.error("ONNX Runtime load failed: %s", exc)
            return False

    def infer(self, input_data):
        """Run inference on input data.

        Parameters
        ----------
        input_data:
            Numpy array of shape (batch, channels, height, width).

        Returns
        -------
        Model outputs as numpy arrays.
        """
        if self._backend == "tensorrt":
            return self._infer_tensorrt(input_data)
        elif self._backend == "onnxruntime":
            return self._infer_onnx(input_data)
        else:
            raise RuntimeError("No model loaded")

    def _infer_tensorrt(self, input_data):
        """TensorRT inference."""
        # In production, allocate CUDA memory and run async inference
        raise NotImplementedError("TensorRT inference requires CUDA memory management")

    def _infer_onnx(self, input_data):
        """ONNX Runtime inference."""
        input_name = self._engine.get_inputs()[0].name
        outputs = self._engine.run(None, {input_name: input_data})
        return outputs

    @property
    def backend(self) -> str | None:
        return self._backend

    @property
    def is_loaded(self) -> bool:
        return self._engine is not None
