# Dify 1.8.0 test references

These files are unchanged references from https://github.com/langgenius/dify/tree/1.8.0/api . See LICENSE in this directory for the upstream license. File names are flattened only for this test fixture.

The regression harness loads the official File and Segment class bodies, CodeNode._run input conversion, and dumps_with_segments. Unrelated service helpers are not exercised. This reproduces the file serialization boundary; it is not a complete Dify workspace or live model test.

Mapping: file_models.py=core/file/models.py; file_enums.py=core/file/enums.py; file_constants.py=core/file/constants.py; segment_types.py=core/variables/types.py; segments.py=core/variables/segments.py; code_node.py=core/workflow/nodes/code/code_node.py; segment_utils.py=core/variables/utils.py.
