# GolfTrace Open

本地运行的 Windows 高尔夫视频轨迹编辑器。导入视频，选择起球帧和白球位置，自动跟踪可见球，再逐帧补点或校正，导出带轨迹线的 MP4。

当前版本：**0.1.0 Alpha**。面向 Windows 10 / 11 x64；实际验证范围见每次发行的验证记录。视频留在本机，应用不上传素材，也不需要账号。

[下载安装版、便携版与校验值](https://github.com/autoloading8822/GolfTraceOpen/releases/tag/v0.1.0) · [本版验证范围](packaging/VALIDATION.md)

## 使用

1. 安装 `GolfTraceOpen-Setup-0.1.0-win-x64.exe`；或解压便携版，运行其中的 `GolfTraceOpen.exe`，保留同目录的 `_internal` 文件夹。
2. 导入 MP4、MOV、AVI、MKV 或 WebM 视频。编码是否可读取决于附带的 OpenCV 解码器。
3. 用时间轴和逐帧按钮定位到球刚离杆、仍然清晰可见的一帧。在画面中点选白球，根据实际大小调整球半径，然后点击自动跟踪。
4. 检查轨迹。球太小、模糊、遮挡或背景干扰可能造成丢失或误跟踪；在相应帧点选真实球位置，补充或覆盖该帧轨迹点。重新自动跟踪会替换当前轨迹，界面会先提示。
5. 保存 `.golftrace.json` 项目以保留轨迹与原视频位置；导出到新的 `.mp4` 文件。首版导出**不保留音频**。

## 能力与边界

- 自动跟踪使用亮色小目标、帧差和运动门限；背景光流与 RANSAC 仿射变换用于补偿相机平移、旋转和均匀缩放。这是传统图像处理方法，未使用已训练的神经网络。
- 连续失配后停止生成球坐标，只继续估计背景运动。球消失后的飞行不会自动补成真实观测；用户补点之间的线是编辑插值。
- 轨迹坐标是解码后画面中的像素，不代表三维弹道、真实距离、球速、自旋或落点测量。
- 建议使用短视频、清晰白球、稳定曝光和纹理丰富的静态背景。极小目标、相机快速运动、复杂人群、弱纹理、明显视差和强压缩可能降低效果。
- 首版按视频标称帧率处理，手机可变帧率素材的精确时间重建未保证。如果出现定位或尾帧解码错误，可从文件菜单生成恒定帧率兼容副本后继续编辑；副本按实际顺序解码帧生成，不保留音频，时间长度可能略有变化，原视频不受影响。
- 导出使用 MPEG-4 Part 2 视频编码，保留源分辨率和标称帧率，但不复制音轨。暂不提供自动更新。

## 来源与验证口径

本项目是自有实现，未取得、反编译或复制商业 App 的程序、模型或资源。公开资料用于确定研究问题，不作为当前软件效果的证明。

- [OpenCV 光流与目标跟踪文档](https://docs.opencv.org/4.12.0/dc/d6b/group__video__track.html)
- [Ace Trace 2023 年教程](https://www.acetrace.app/blog/tutorial-acetrace-golf-and-disc-golf/)介绍检测、预测和人工校正的产品流程；本项目不声称实现其内部算法。
- 测试中的合成视频用于测量已知运动误差，不等于真实球场准确率。真实素材、程序截图、安装与导出验证单独记录，不把未运行检查计为通过。

## 开发与构建

使用 Python 3.12 x64，依赖版本见 `requirements-lock.txt`。在项目虚拟环境中安装依赖并运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m golftrace
```

测试及 Windows 打包：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build_windows.ps1
```

打包需要项目 `tools/innosetup/ISCC.exe`；构建环境和第三方声明见 `packaging/`。构建脚本默认先运行测试。安装包、便携 ZIP 与校验值输出到 `release/`。构建产物、工具、虚拟环境、私人素材和本地研究记录不加入源码仓库。

## 许可证

本项目自有源码采用 MIT 许可证。PySide6 / Qt、OpenCV、NumPy、Python 等组件保留各自许可证；发布包附第三方声明与实际依赖版本。Qt 以可替换的动态库方式分发。
