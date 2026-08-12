import os
import sys
import base64


# 将文件生成base64编码
def file_to_base64(source_file, target_file):
    # 读取文件内容
    with open(source_file, 'rb') as f:
        file_content = f.read()
    # 将文件内容转换为base64编码的字符串
    base64_content = base64.b64encode(file_content)
    # 将base64编码的字符串写入新的文件
    with open(target_file, 'wb') as f:
        f.write(base64_content)
        print(f"{target_file}写入结束")


def base64_txt_to_file(source_file, target_file):
    # 读取文件内容
    with open(source_file, 'rb') as f:
        file_content = f.read().strip()
        # print(file_content)
    # 将文件内容转换为base64编码的字符串
    base64_content = base64.b64decode(file_content)
    # 将base64编码的字符串写入新的文件
    with open(target_file, 'wb') as f:
        f.write(base64_content)
        print(f"{target_file}写入结束")


############################用于将文件转化成base64编码的文本#######################################################################

arr = [
"C:\\Users\\15385\\myproject\\sleuth.zip"
]
for x in arr:
    source_file = x
    target_file_suffix = ".txt"
    target_file = source_file + target_file_suffix
    file_to_base64(source_file, target_file)

#############################用于将base64编码的文本转化成文件######################################################################
# source_file="D:/base64/base64.txt"
# target_file_suffix=".7z"
# source_path=source_file.split(".")
# source_path.pop()
# source_path='.'.join(source_path)
# target_file=(source_path+target_file_suffix)
# base64_txt_to_file(source_file,target_file)