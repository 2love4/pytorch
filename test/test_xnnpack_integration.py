from __future__ import division

import unittest

import torch
import torch.backends.xnnpack
from torch.nn import functional as F
from torch.testing import FileCheck
import torch.testing._internal.hypothesis_utils as hu
from torch.testing._internal.common_utils import TestCase, run_tests
from hypothesis import given, assume
from hypothesis import strategies as st
import io


@unittest.skipUnless(torch.backends.xnnpack.enabled,
                     " XNNPACK must be enabled for these tests."
                     " Please build with USE_XNNPACK=1.")
class TestXNNPACKOps(TestCase):
    @given(batch_size=st.integers(0, 3),
           data_shape=hu.array_shapes(1, 3, 2, 64),
           weight_output_dim=st.integers(2, 64),
           use_bias=st.booleans())
    def test_linear(self, batch_size, data_shape, weight_output_dim, use_bias):
        data_shape = [batch_size] + list(data_shape)
        input_data = torch.rand(data_shape)
        weight = torch.rand((weight_output_dim, data_shape[-1]))
        if use_bias:
            bias = torch.rand((weight_output_dim))
        else:
            bias = None
        ref_result = F.linear(input_data, weight, bias)
        packed_weight_bias = torch.ops.prepacked.linear_clamp_prepack(weight, bias)
        output_linearprepacked = torch.ops.prepacked.linear_clamp_run(input_data, packed_weight_bias)
        torch.testing.assert_allclose(ref_result, output_linearprepacked, rtol=1e-2, atol=1e-3)

    @given(batch_size=st.integers(0, 3),
           input_channels_per_group=st.integers(1, 32),
           height=st.integers(5, 64),
           width=st.integers(5, 64),
           output_channels_per_group=st.integers(1, 32),
           groups=st.integers(1, 16),
           kernel_h=st.integers(1, 7),
           kernel_w=st.integers(1, 7),
           stride_h=st.integers(1, 2),
           stride_w=st.integers(1, 2),
           pad_h=st.integers(0, 2),
           pad_w=st.integers(0, 2),
           dilation=st.integers(1, 2),
           use_bias=st.booleans(),
           format=st.sampled_from([None, torch.preserve_format, torch.contiguous_format, torch.channels_last]))
    def test_conv2d(self,
                    batch_size,
                    input_channels_per_group,
                    height,
                    width,
                    output_channels_per_group,
                    groups,
                    kernel_h,
                    kernel_w,
                    stride_h,
                    stride_w,
                    pad_h,
                    pad_w,
                    dilation,
                    use_bias,
                    format):
        input_channels = input_channels_per_group * groups
        output_channels = output_channels_per_group * groups
        kernels = (kernel_h, kernel_w)
        strides = (stride_h, stride_w)
        paddings = (pad_h, pad_w)
        dilations = (dilation, dilation)
        assume(height + 2 * paddings[0]
               >= dilations[0] * (kernels[0] - 1) + 1)
        assume(width + 2 * paddings[1]
               >= dilations[1] * (kernels[1] - 1) + 1)

        input_data = torch.rand((batch_size, input_channels, height, width))
        if (format is not None):
            input_data = input_data.contiguous(memory_format=format)
        weight = torch.rand((output_channels, input_channels_per_group, kernel_h, kernel_w))
        bias = None
        if use_bias:
            bias = torch.rand((output_channels))

        ref_result = F.conv2d(input_data, weight, bias,
                              strides, paddings, dilations, groups)
        packed_weight_bias = torch.ops.prepacked.conv2d_clamp_prepack(weight, bias,
                                                                      strides, paddings, dilations, groups)
        xnnpack_result = torch.ops.prepacked.conv2d_clamp_run(input_data, packed_weight_bias)
        torch.testing.assert_allclose(ref_result, xnnpack_result, rtol=1e-2, atol=1e-3)


@unittest.skipUnless(torch.backends.xnnpack.enabled,
                     " XNNPACK must be enabled for these tests."
                     " Please build with USE_XNNPACK=1.")
class TestXNNPACKSerDes(TestCase):
    @given(batch_size=st.integers(0, 3),
           data_shape=hu.array_shapes(1, 3, 2, 64),
           weight_output_dim=st.integers(2, 64),
           use_bias=st.booleans())
    def test_linear(self, batch_size, data_shape, weight_output_dim, use_bias):
        class Linear(torch.nn.Module):
            def __init__(self, weight, bias=None):
                super(Linear, self).__init__()
                self.weight = weight
                self.bias = bias

            def forward(self, x):
                return F.linear(x, self.weight, self.bias)

        class LinearPrePacked(torch.nn.Module):
            def __init__(self, weight, bias=None):
                super(LinearPrePacked, self).__init__()
                self.packed_weight_bias = torch.ops.prepacked.linear_clamp_prepack(weight, bias)

            def forward(self, x):
                return torch.ops.prepacked.linear_clamp_run(x, self.packed_weight_bias)

        data_shape = [batch_size] + list(data_shape)
        weight = torch.rand((weight_output_dim, data_shape[-1]))
        if use_bias:
            bias = torch.rand((weight_output_dim))
        else:
            bias = None
        scripted_linear = torch.jit.script(Linear(weight, bias))
        scripted_linear_clamp_prepacked = torch.jit.script(LinearPrePacked(weight, bias))
        input_data = torch.rand(data_shape)
        ref_result = scripted_linear(input_data)
        output_linearprepacked = scripted_linear_clamp_prepacked(input_data)
        torch.testing.assert_allclose(ref_result, output_linearprepacked, rtol=1e-2, atol=1e-3)

        # Serialize the modules and then deserialize
        input_data = torch.rand(data_shape)
        buffer = io.BytesIO()
        torch.jit.save(scripted_linear, buffer)
        buffer.seek(0)
        deserialized_linear = torch.jit.load(buffer)
        buffer = io.BytesIO()
        torch.jit.save(scripted_linear_clamp_prepacked, buffer)
        buffer.seek(0)
        deserialized_linear_clamp_prepacked = torch.jit.load(buffer)
        ref_result = deserialized_linear(input_data)
        output_linearprepacked = deserialized_linear_clamp_prepacked(input_data)
        torch.testing.assert_allclose(ref_result, output_linearprepacked, rtol=1e-2, atol=1e-3)

    @given(batch_size=st.integers(0, 3),
           input_channels_per_group=st.integers(1, 32),
           height=st.integers(5, 64),
           width=st.integers(5, 64),
           output_channels_per_group=st.integers(1, 32),
           groups=st.integers(1, 16),
           kernel_h=st.integers(1, 7),
           kernel_w=st.integers(1, 7),
           stride_h=st.integers(1, 2),
           stride_w=st.integers(1, 2),
           pad_h=st.integers(0, 2),
           pad_w=st.integers(0, 2),
           dilation=st.integers(1, 2),
           use_bias=st.booleans(),
           format=st.sampled_from([None, torch.preserve_format, torch.contiguous_format, torch.channels_last]))
    def test_conv2d(self,
                    batch_size,
                    input_channels_per_group,
                    height,
                    width,
                    output_channels_per_group,
                    groups,
                    kernel_h,
                    kernel_w,
                    stride_h,
                    stride_w,
                    pad_h,
                    pad_w,
                    dilation,
                    use_bias,
                    format):
        class Conv2D(torch.nn.Module):
            def __init__(self, weight, bias, strides, paddings, dilations, groups):
                super(Conv2D, self).__init__()
                self.weight = weight
                self.bias = bias
                self.strides = strides
                self.paddings = paddings
                self.dilations = dilations
                self.groups = groups

            def forward(self, x):
                return F.conv2d(x, self.weight, self.bias,
                                self.strides, self.paddings, self.dilations, self.groups)

        class Conv2DPrePacked(torch.nn.Module):
            def __init__(self, weight, bias, strides, paddings, dilations, groups):
                super(Conv2DPrePacked, self).__init__()
                self.packed_weight_bias = torch.ops.prepacked.conv2d_clamp_prepack(weight, bias,
                                                                                   strides, paddings, dilations, groups)

            def forward(self, x):
                return torch.ops.prepacked.conv2d_clamp_run(x, self.packed_weight_bias)

        input_channels = input_channels_per_group * groups
        output_channels = output_channels_per_group * groups
        kernels = (kernel_h, kernel_w)
        strides = (stride_h, stride_w)
        paddings = (pad_h, pad_w)
        dilations = (dilation, dilation)
        assume(height + 2 * paddings[0] >=
               dilations[0] * (kernels[0] - 1) + 1)
        assume(width + 2 * paddings[1] >=
               dilations[1] * (kernels[1] - 1) + 1)

        input_data = torch.rand((batch_size, input_channels, height, width))
        if (format is not None):
            input_data = input_data.contiguous(memory_format=format)
        weight = torch.rand((output_channels, input_channels_per_group, kernel_h, kernel_w))
        bias = None
        if use_bias:
            bias = torch.rand((output_channels))

        scripted_conv2d = torch.jit.script(Conv2D(weight, bias,
                                                  strides, paddings, dilations, groups))
        scripted_conv2d_clamp_prepacked = torch.jit.script(Conv2DPrePacked(
            weight, bias, strides, paddings, dilations, groups))
        ref_result = scripted_conv2d(input_data)
        xnnpack_result = scripted_conv2d_clamp_prepacked(input_data)
        torch.testing.assert_allclose(ref_result, xnnpack_result, rtol=1e-2, atol=1e-3)

        # Serialize the modules and then deserialize
        input_data = torch.rand((batch_size, input_channels, height, width))
        if (format is not None):
            input_data = input_data.contiguous(memory_format=format)
        buffer = io.BytesIO()
        torch.jit.save(scripted_conv2d, buffer)
        buffer.seek(0)
        deserialized_conv2d = torch.jit.load(buffer)
        buffer = io.BytesIO()
        torch.jit.save(scripted_conv2d_clamp_prepacked, buffer)
        buffer.seek(0)
        deserialized_conv2d_clamp_prepacked = torch.jit.load(buffer)
        ref_result = deserialized_conv2d(input_data)
        xnnpack_result = deserialized_conv2d_clamp_prepacked(input_data)
        torch.testing.assert_allclose(ref_result, xnnpack_result, rtol=1e-2, atol=1e-3)

    @given(batch_size=st.integers(0, 3),
           input_channels_per_group=st.integers(1, 32),
           height=st.integers(5, 64),
           width=st.integers(5, 64),
           output_channels_per_group=st.integers(1, 32),
           groups=st.integers(1, 16),
           kernel_h=st.integers(1, 7),
           kernel_w=st.integers(1, 7),
           stride_h=st.integers(1, 2),
           stride_w=st.integers(1, 2),
           pad_h=st.integers(0, 2),
           pad_w=st.integers(0, 2),
           dilation=st.integers(1, 2),
           linear_weight_output_dim=st.integers(2, 64),
           use_bias=st.booleans(),
           format=st.sampled_from([None, torch.preserve_format, torch.contiguous_format, torch.channels_last]))
    def test_combined_model(self,
                            batch_size,
                            input_channels_per_group,
                            height,
                            width,
                            output_channels_per_group,
                            groups,
                            kernel_h,
                            kernel_w,
                            stride_h,
                            stride_w,
                            pad_h,
                            pad_w,
                            dilation,
                            linear_weight_output_dim,
                            use_bias,
                            format):
        class M(torch.nn.Module):
            def __init__(self, conv_weight, conv_bias, linear_weight, linear_bias,
                         strides, paddings, dilations, groups):
                super(M, self).__init__()
                self.conv_weight = conv_weight
                self.conv_bias = conv_bias
                self.linear_weight = linear_weight
                self.linear_bias = linear_bias
                self.strides = strides
                self.paddings = paddings
                self.dilations = dilations
                self.groups = groups

            def forward(self, x):
                o = F.conv2d(x, self.conv_weight, self.conv_bias,
                             self.strides, self.paddings, self.dilations, self.groups)
                o = o.permute([0, 2, 3, 1])
                o = F.linear(o, self.linear_weight, self.linear_bias)
                return F.relu(o)

        class MPrePacked(torch.nn.Module):
            def __init__(self, conv_weight, conv_bias, linear_weight, linear_bias,
                         strides, paddings, dilations, groups):
                super(MPrePacked, self).__init__()
                self.conv2d_clamp_run_weight_bias = \
                    torch.ops.prepacked.conv2d_clamp_prepack(conv_weight, conv_bias,
                                                             strides, paddings, dilations, groups)
                self.linear_clamp_run_weight_bias = \
                    torch.ops.prepacked.linear_clamp_prepack(linear_weight, linear_bias)

            def forward(self, x):
                o = torch.ops.prepacked.conv2d_clamp_run(x, self.conv2d_clamp_run_weight_bias)
                o = o.permute([0, 2, 3, 1])
                o = torch.ops.prepacked.linear_clamp_run(o, self.linear_clamp_run_weight_bias)
                return F.relu(o)

        input_channels = input_channels_per_group * groups
        output_channels = output_channels_per_group * groups
        kernels = (kernel_h, kernel_w)
        strides = (stride_h, stride_w)
        paddings = (pad_h, pad_w)
        dilations = (dilation, dilation)
        assume(height + 2 * paddings[0]
               >= dilations[0] * (kernels[0] - 1) + 1)
        assume(width + 2 * paddings[1]
               >= dilations[1] * (kernels[1] - 1) + 1)

        input_data = torch.rand((batch_size, input_channels, height, width))
        if (format is not None):
            input_data = input_data.contiguous(memory_format=format)
        conv_weight = torch.rand((output_channels, input_channels_per_group, kernel_h, kernel_w))
        conv_bias = None
        if use_bias:
            conv_bias = torch.rand((output_channels))

        # This is done just to find the output shape of the result
        # so that the shape of weight for the following linear layer
        # can be determined.
        result = F.conv2d(input_data, conv_weight, conv_bias,
                          strides, paddings, dilations, groups)
        linear_input_shape = result.shape[1]

        linear_weight = torch.rand((linear_weight_output_dim, linear_input_shape))
        linear_bias = None
        if use_bias:
            linear_bias = torch.rand((linear_weight_output_dim))

        scripted_m = torch.jit.script(M(conv_weight, conv_bias, linear_weight,
                                        linear_bias, strides, paddings, dilations, groups))
        scripted_m_prepacked = torch.jit.script(
            MPrePacked(
                conv_weight,
                conv_bias,
                linear_weight,
                linear_bias,
                strides,
                paddings,
                dilations,
                groups))
        ref_result = scripted_m(input_data)
        xnnpack_result = scripted_m_prepacked(input_data)
        torch.testing.assert_allclose(ref_result, xnnpack_result, rtol=1e-2, atol=1e-3)

        # Serialize the modules and then deserialize
        input_data = torch.rand((batch_size, input_channels, height, width))
        input_data = input_data.contiguous(memory_format=torch.channels_last)
        buffer = io.BytesIO()
        torch.jit.save(scripted_m, buffer)
        buffer.seek(0)
        deserialized_m = torch.jit.load(buffer)
        buffer = io.BytesIO()
        torch.jit.save(scripted_m_prepacked, buffer)
        buffer.seek(0)
        deserialized_m_prepacked = torch.jit.load(buffer)
        ref_result = deserialized_m(input_data)
        xnnpack_result = deserialized_m_prepacked(input_data)
        torch.testing.assert_allclose(ref_result, xnnpack_result, rtol=1e-2, atol=1e-3)


@unittest.skipUnless(torch.backends.xnnpack.enabled,
                     " XNNPACK must be enabled for these tests."
                     " Please build with USE_XNNPACK=1.")
class TestXNNPACKRewritePass(TestCase):
    def test_linear(self):
        def validate_transformed_module(
                module_instance,
                pattern_count_map,
                data_shape,
                prepack_removal=False,
                fuse_clamping_ops=False):
            scripted_model = torch.jit.script(module_instance)
            scripted_model.eval()
            input_data = torch.normal(1, 20, size=data_shape)
            ref_result = scripted_model(input_data)
            torch._C._jit_pass_insert_prepacked_ops(scripted_model._c)
            if fuse_clamping_ops or prepack_removal:
                scripted_model._c = torch._C._freeze_module(scripted_model._c)
            if fuse_clamping_ops:
                torch._C._jit_pass_fuse_clamp_w_prepacked_linear_conv(scripted_model._c)
            if (prepack_removal):
                torch._C._jit_pass_fold_prepacking_ops(scripted_model._c)

            buffer = io.BytesIO()
            torch.jit.save(scripted_model, buffer)
            buffer.seek(0)
            deserialized_scripted_model = torch.jit.load(buffer)
            for pattern, v in pattern_count_map.items():
                if (v == 0):
                    FileCheck().check(pattern).run(deserialized_scripted_model.graph)
                elif (v == -1):
                    FileCheck().check_not(pattern).run(deserialized_scripted_model.graph)
                else:
                    FileCheck().check_count(pattern, v, exactly=True).run(deserialized_scripted_model.graph)
            xnnpack_result = deserialized_scripted_model(input_data)
            torch.testing.assert_allclose(ref_result, xnnpack_result, rtol=1e-2, atol=1e-3)

        data_shape = [2, 3, 32]
        weight_output_dim = 24
        weight_shape = (weight_output_dim, data_shape[-1])

        class Linear(torch.nn.Module):
            def __init__(self):
                super(Linear, self).__init__()
                self.weight = torch.nn.Parameter(torch.Tensor(torch.rand(weight_shape)), requires_grad=False)
                self.bias = torch.nn.Parameter(torch.Tensor(torch.rand((weight_output_dim))), requires_grad=False)

            def forward(self, x):
                return F.linear(x, self.weight, self.bias)

        class LinearNoBias(torch.nn.Module):
            def __init__(self):
                super(LinearNoBias, self).__init__()
                self.weight = torch.nn.Parameter(torch.Tensor(torch.rand(weight_shape)), requires_grad=False)

            def forward(self, x):
                return F.linear(x, self.weight, None)

        # Linear with bias pattern.
        pattern_count_map = {"Tensor = prim::CallFunction": -1,
                             "prepacked::linear_clamp_prepack": 1,
                             "prepacked::linear_clamp_run": 1}
        validate_transformed_module(Linear(), pattern_count_map, data_shape)
        validate_transformed_module(LinearNoBias(), pattern_count_map, data_shape)

        # Conv params
        batch_size = 2
        input_channels_per_group = 6
        height = 16
        width = 16
        output_channels_per_group = 6
        groups = 4
        kernel_h = kernel_w = 3
        stride_h = stride_w = 1
        pad_h = pad_w = 1
        dilation = 1
        input_channels = input_channels_per_group * groups
        output_channels = output_channels_per_group * groups
        kernels = (kernel_h, kernel_w)
        strides = (stride_h, stride_w)
        paddings = (pad_h, pad_w)
        dilations = (dilation, dilation)
        conv_weight_shape = (output_channels, input_channels_per_group, kernel_h, kernel_w)
        conv_bias_shape = (output_channels)

        class Conv2D(torch.nn.Module):
            def __init__(self):
                super(Conv2D, self).__init__()
                self.weight = torch.nn.Parameter(torch.Tensor(torch.rand(conv_weight_shape)), requires_grad=False)
                self.bias = torch.nn.Parameter(torch.Tensor(torch.rand(conv_bias_shape)), requires_grad=False)
                self.strides = strides
                self.paddings = paddings
                self.dilations = dilations
                self.groups = groups

            def forward(self, x):
                return F.conv2d(x, self.weight, self.bias,
                                self.strides, self.paddings, self.dilations, self.groups)

        data_shape = (batch_size, input_channels, height, width)
        pattern_count_map = {"Tensor = aten::conv2d": -1,
                             "prepacked::conv2d_clamp_prepack": 1,
                             "prepacked::conv2d_clamp_run": 1}
        validate_transformed_module(Conv2D(), pattern_count_map, data_shape)

        input_data = torch.rand((batch_size, input_channels, height, width))
        conv_weight = torch.rand((output_channels, input_channels_per_group, kernel_h, kernel_w))
        conv_bias = torch.rand((output_channels))
        result = F.conv2d(input_data, conv_weight, conv_bias,
                          strides, paddings, dilations, groups)
        linear_input_shape = result.shape[1]
        linear_weight_shape = (weight_output_dim, linear_input_shape)

        class M(torch.nn.Module):
            def __init__(self, activation_fn=F.relu):
                super(M, self).__init__()
                self.conv_weight = torch.nn.Parameter(torch.Tensor(torch.rand(conv_weight_shape)), requires_grad=False)
                self.conv_bias = torch.nn.Parameter(torch.Tensor(torch.rand((conv_bias_shape))), requires_grad=False)
                self.linear_weight = torch.nn.Parameter(torch.Tensor(torch.rand(linear_weight_shape)), requires_grad=False)
                self.linear_bias = torch.nn.Parameter(torch.Tensor(torch.rand((weight_output_dim))), requires_grad=False)
                self.strides = strides
                self.paddings = paddings
                self.dilations = dilations
                self.groups = groups
                self.activation_fn = activation_fn

            def forward(self, x):
                o = F.conv2d(x, self.conv_weight, self.conv_bias,
                             self.strides, self.paddings, self.dilations, self.groups)
                o = self.activation_fn(o)
                o = o.permute([0, 2, 3, 1])
                o = F.linear(o, self.linear_weight, self.linear_bias)
                return self.activation_fn(o)

        pattern_count_map = {"Tensor = aten::conv2d": -1,
                             "prepacked::conv2d_clamp_prepack": 1,
                             "prepacked::conv2d_clamp_run": 1,
                             "prepacked::linear_clamp_prepack": 1,
                             "prepacked::linear_clamp_run": 1}
        validate_transformed_module(M(), pattern_count_map, data_shape)
        pattern_count_map["prepacked::conv2d_clamp_prepack"] = -1
        pattern_count_map["Tensor = prim::CallFunction"] = -1
        pattern_count_map["prepacked::linear_clamp_prepack"] = -1
        validate_transformed_module(M(), pattern_count_map, data_shape, prepack_removal=True)

        # Not inplace relu fusion test.
        pattern_count_map = {"aten::relu": 2,
                             "prepacked::conv2d_clamp_prepack": -1,
                             "prepacked::conv2d_clamp_run": 1,
                             "prepacked::linear_clamp_prepack": -1,
                             "prepacked::linear_clamp_run": 1}
        validate_transformed_module(M(), pattern_count_map, data_shape, prepack_removal=True)
        pattern_count_map["prepacked::conv2d_clamp_prepack"] = -1
        pattern_count_map["prepacked::linear_clamp_prepack"] = -1
        pattern_count_map["aten::relu"] = -1
        validate_transformed_module(M(), pattern_count_map, data_shape, prepack_removal=True, fuse_clamping_ops=True)

        # Inplace relu fusion test.
        pattern_count_map = {"aten::relu": 2,
                             "prepacked::conv2d_clamp_prepack": -1,
                             "prepacked::conv2d_clamp_run": 1,
                             "prepacked::linear_clamp_prepack": -1,
                             "prepacked::linear_clamp_run": 1}
        validate_transformed_module(M(F.relu_), pattern_count_map, data_shape, prepack_removal=True)
        pattern_count_map["prepacked::conv2d_clamp_prepack"] = -1
        pattern_count_map["prepacked::linear_clamp_prepack"] = -1
        pattern_count_map["aten::relu"] = -1
        validate_transformed_module(M(F.relu_), pattern_count_map, data_shape,
                                    prepack_removal=True, fuse_clamping_ops=True)

        # Not inplace hardtanh fusion test.
        pattern_count_map = {"aten::hardtanh": 2,
                             "prepacked::conv2d_clamp_prepack": -1,
                             "prepacked::conv2d_clamp_run": 1,
                             "prepacked::linear_clamp_prepack": -1,
                             "prepacked::linear_clamp_run": 1}
        validate_transformed_module(M(F.hardtanh), pattern_count_map, data_shape, prepack_removal=True)
        pattern_count_map["prepacked::conv2d_clamp_prepack"] = -1
        pattern_count_map["prepacked::linear_clamp_prepack"] = -1
        pattern_count_map["aten::hardtanh"] = -1
        validate_transformed_module(M(F.hardtanh), pattern_count_map, data_shape,
                                    prepack_removal=True, fuse_clamping_ops=True)

        # Inplace hardtanh fusion test.
        pattern_count_map = {"aten::hardtanh_": 2,
                             "prepacked::conv2d_clamp_prepack": -1,
                             "prepacked::conv2d_clamp_run": 1,
                             "prepacked::linear_clamp_prepack": -1,
                             "prepacked::linear_clamp_run": 1}
        validate_transformed_module(M(F.hardtanh_), pattern_count_map, data_shape, prepack_removal=True)
        pattern_count_map["prepacked::conv2d_clamp_prepack"] = -1
        pattern_count_map["prepacked::linear_clamp_prepack"] = -1
        pattern_count_map["aten::hardtanh_"] = -1
        validate_transformed_module(M(F.hardtanh_), pattern_count_map, data_shape,
                                    prepack_removal=True, fuse_clamping_ops=True)

        class MFusionAntiPattern(torch.nn.Module):
            def __init__(self):
                super(MFusionAntiPattern, self).__init__()
                self.linear_weight = torch.nn.Parameter(torch.Tensor(torch.rand(linear_weight_shape)), requires_grad=False)
                self.linear_bias = torch.nn.Parameter(torch.Tensor(torch.rand((weight_output_dim))), requires_grad=False)
                self.strides = strides
                self.paddings = paddings
                self.dilations = dilations
                self.groups = groups

            def forward(self, x):
                o = F.linear(x, self.linear_weight, self.linear_bias)
                o = F.relu(o)
                o = F.hardtanh(o)
                return o

        # Unfusable hardtanh.
        pattern_count_map = {"aten::hardtanh": 1,  # hardtanh cannot be.
                             "aten::relu": -1,  # relu is fused.
                             "prepacked::linear_clamp_prepack": -1,
                             "prepacked::linear_clamp_run": 1}
        validate_transformed_module(MFusionAntiPattern(), pattern_count_map, (16, linear_weight_shape[1]),
                                    prepack_removal=True, fuse_clamping_ops=True)

        class MFusionAntiPatternParamMinMax(torch.nn.Module):
            def __init__(self):
                super(MFusionAntiPatternParamMinMax, self).__init__()
                self.linear_weight = torch.nn.Parameter(torch.Tensor(torch.rand(linear_weight_shape)), requires_grad=False)
                self.linear_bias = torch.nn.Parameter(torch.Tensor(torch.rand((weight_output_dim))), requires_grad=False)
                self.strides = strides
                self.paddings = paddings
                self.dilations = dilations
                self.groups = groups

            def forward(self, x):
                min = x[0, 0]
                max = min + 10
                o = F.linear(x, self.linear_weight, self.linear_bias)
                o = F.hardtanh(o, min, max)
                return o

        # Unfusable hardtanh.
        pattern_count_map = {"aten::hardtanh": 1,  # hardtanh cannot be.
                             "prepacked::linear_clamp_prepack": -1,
                             "prepacked::linear_clamp_run": 1}
        validate_transformed_module(MFusionAntiPatternParamMinMax(), pattern_count_map, (16, linear_weight_shape[1]),
                                    prepack_removal=True, fuse_clamping_ops=True)


if __name__ == "__main__":
    run_tests()
