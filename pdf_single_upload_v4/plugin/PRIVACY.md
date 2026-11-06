# Data handling

The tools read the PDF uploaded to Dify using the official file transport. PDF
text extraction, vector association, rendering and workbook generation run in
the Dify plugin runtime. This plugin makes no calls to external recognition,
product, price, database or CAD services. It contains no model credentials.

The paired workflow sends rendered images and native text context to the model
provider selected in the user's Dify workspace. Dify stores uploaded and generated
files according to the workspace configuration. The plugin does not log PDF
contents or signed file URLs, and does not keep an additional on-disk cache.
