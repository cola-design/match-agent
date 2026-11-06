V3.1修复Dify1.8文件对象传递错误。

1. 导入pdf_recognition_test_v3_1.yml为新应用。
2. 两个视觉节点选择你已有的qwen3-vl-plus。
3. 上传原来batch目录的evidence.txt和全部PNG，沿用原项目要求，发送“开始识图”。

原TXT和图片无需重新生成，Python依赖无需重新安装。
本次修复文件类型兼容问题，模型识图准确性仍需实际运行核对。
