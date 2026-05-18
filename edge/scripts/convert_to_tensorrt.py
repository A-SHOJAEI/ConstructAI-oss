"""Convert ONNX model to TensorRT engine for Jetson deployment."""
from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def convert_onnx_to_tensorrt(
    onnx_path: str,
    engine_path: str,
    precision: str = "int8",
    max_batch_size: int = 4,
    workspace_gb: float = 4.0,
    calibration_data: str | None = None,
) -> bool:
    """Convert ONNX model to TensorRT engine.

    Parameters
    ----------
    onnx_path:
        Path to the input ONNX model.
    engine_path:
        Path for the output TensorRT engine file.
    precision:
        Inference precision: "fp32", "fp16", or "int8".
    max_batch_size:
        Maximum batch size for the engine.
    workspace_gb:
        GPU workspace allocation in GB.
    calibration_data:
        Path to calibration images directory (required for INT8).

    Returns
    -------
    True if conversion succeeded.
    """
    try:
        import tensorrt as trt  # noqa: F401
    except ImportError:
        logger.error("TensorRT not available. Run on a system with TensorRT installed.")
        return False

    TRT_LOGGER = trt.Logger(trt.Logger.INFO)

    builder = trt.Builder(TRT_LOGGER)
    network = builder.create_network(
        1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
    )
    parser = trt.OnnxParser(network, TRT_LOGGER)

    logger.info("Parsing ONNX model: %s", onnx_path)
    with open(onnx_path, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                logger.error("ONNX parse error: %s", parser.get_error(i))
            return False

    config = builder.create_builder_config()
    config.set_memory_pool_limit(
        trt.MemoryPoolType.WORKSPACE, int(workspace_gb * (1 << 30))
    )

    if precision == "fp16":
        if builder.platform_has_fast_fp16:
            config.set_flag(trt.BuilderFlag.FP16)
            logger.info("FP16 mode enabled")
        else:
            logger.warning("FP16 not supported on this platform, using FP32")
    elif precision == "int8":
        if builder.platform_has_fast_int8:
            config.set_flag(trt.BuilderFlag.INT8)
            config.set_flag(trt.BuilderFlag.FP16)
            logger.info("INT8 mode enabled (with FP16 fallback)")
            if calibration_data:
                logger.info("Calibration data: %s", calibration_data)
                # In production, implement IInt8EntropyCalibrator2 here
        else:
            logger.warning("INT8 not supported, falling back to FP16")
            if builder.platform_has_fast_fp16:
                config.set_flag(trt.BuilderFlag.FP16)

    profile = builder.create_optimization_profile()
    input_tensor = network.get_input(0)
    input_shape = input_tensor.shape
    min_shape = (1, input_shape[1], input_shape[2], input_shape[3])
    opt_shape = (max_batch_size, input_shape[1], input_shape[2], input_shape[3])
    max_shape = (max_batch_size, input_shape[1], input_shape[2], input_shape[3])
    profile.set_shape(input_tensor.name, min_shape, opt_shape, max_shape)
    config.add_optimization_profile(profile)

    logger.info("Building TensorRT engine (this may take several minutes)...")
    serialized_engine = builder.build_serialized_network(network, config)

    if serialized_engine is None:
        logger.error("Failed to build TensorRT engine")
        return False

    with open(engine_path, "wb") as f:
        f.write(serialized_engine)

    logger.info("TensorRT engine saved to: %s", engine_path)
    return True


def main():
    parser = argparse.ArgumentParser(description="Convert ONNX to TensorRT")
    parser.add_argument("--onnx", required=True, help="Input ONNX model path")
    parser.add_argument("--output", required=True, help="Output engine path")
    parser.add_argument("--precision", default="int8", choices=["fp32", "fp16", "int8"])
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--workspace", type=float, default=4.0, help="Workspace GB")
    parser.add_argument("--calibration-data", default=None)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    success = convert_onnx_to_tensorrt(
        onnx_path=args.onnx,
        engine_path=args.output,
        precision=args.precision,
        max_batch_size=args.batch_size,
        workspace_gb=args.workspace,
        calibration_data=args.calibration_data,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
