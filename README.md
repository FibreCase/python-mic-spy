# mic-spy

Armbian 上持续保存麦克风音频，通过 sherpa-onnx Zipformer2 CTC 实时识别中文，将句段写入 SQLite。使用你已测试的模型和 `plughw:0,0`。暂不包含 Web 面板。

## 两台设备：采集者 / 处理者

两端都安装项目和 FFmpeg：

```sh
sudo apt-get install ffmpeg
uv sync --locked --no-dev
```

先在处理设备启动（模型目录放在处理端）：

```sh
uv run mic-spy --data ./data processor --listen udp://0.0.0.0:5000
```

再在 Armbian 采集设备启动，将地址替换成处理设备的局域网 IP：

```sh
uv run mic-spy collector --device plughw:0,0 --target udp://192.168.1.100:5000
```

采集者仅启动 FFmpeg：ALSA → 48kHz 单声道 → Opus（默认 24000 bit/s，20ms 帧）→ MPEG-TS → UDP，不加载识别模型、不建立数据库。可用 `--bitrate 32000` 修改码率。处理者启动 FFmpeg 接收并解码为 48kHz int16 PCM，随后通过原有识别、存储模块写入 WAV 和 SQLite。查询与导出命令不变。Python 依赖目前共用同一安装集合，采集身份不会导入 numpy 或 sherpa 的模型运行库。

处理端需允许所选 UDP 端口。一个监听端口只对应一个采集端；多设备应分配不同端口、数据目录及处理进程。采集端 FFmpeg 需支持 ALSA、libopus，可运行 `ffmpeg -h encoder=libopus` 检查编码器。现有 `run` 命令仍用于在同一设备直接采集和识别。

处理端默认 15 秒没有 UDP 包会退出接收并在 5 秒后重试，可通过 `--receive-timeout 30` 修改。每次重试创建新的录音会话。采集端也会在 FFmpeg 退出后重试；UDP 没有确认机制，发送进程正常不代表处理端已收到。两端 SIGTERM/Ctrl+C 都会关闭其 FFmpeg 子进程。

**当前是实时传输版**：没有采集端本地缓存、断网补传、采集端会话元数据或加密认证。使用受信任的局域网。处理端保存的是 Opus 解码后的音频，不是采集端原始 PCM。

网络模式下，数据库的 UTC 起点是处理端收到第一块解码音频的时间，之后按采样计数。文字与处理端保存音频的回放位置对应，但不能作为采集设备的精确墙钟时间。UDP 丢包或短暂中断可能产生丢失或时间压缩；只有触发超时重启的中断才分成独立会话。接收缓冲溢出默认报错重启，不静默继续。长期完整归档需要后续增加本地缓存、补传和采集时间元数据。

两端 systemd 示例分别在 `deploy/mic-spy-collector.service`、`deploy/mic-spy-processor.service`。采集端修改目标 IP，处理端修改模型路径或把模型放在 `/opt/mic-spy` 下。按下文创建用户、安装对应服务：

```sh
# 采集端
sudo cp deploy/mic-spy-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mic-spy-collector

# 处理端
sudo cp deploy/mic-spy-processor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mic-spy-processor
```

## 启动

需要 64 位 Linux、Python 3.11+、uv，以及支持该架构的 sherpa-onnx wheel。先将已有模型目录复制到项目根目录。

```sh
sudo apt-get install alsa-utils
uv sync --locked
uv run mic-spy --data ./data run --device plughw:0,0
```

可通过 `--model`、`--tokens` 指定绝对路径；`--threads 2` 设置推理线程，`--segment-seconds 300` 设置 WAV 分段长度。音频与识别文字默认缓存在内存中，每 60 秒集中写入一次。Ctrl+C 或 SIGTERM 会立即保存剩余音频及最后的识别结果并关闭音频文件。

## 查询和回放

```sh
uv run mic-spy --data ./data query --text 测试 --limit 20
uv run mic-spy --data ./data export 1 ./clip.wav
aplay ./clip.wav
```

查询输出 JSON 行，含文字、UTC Unix 纳秒起止时间和会话内采样位置。`export` 按文字 ID 导出完整句段，可以跨越 WAV 文件边界。查询支持中文子串。后续面板可将纳秒转换成本地时间，并根据 ID 提供回放。

## 数据结构与时间语义

- `data/archive.sqlite3`：WAL 模式数据库，支持采集时另开只读连接查询。
- `sessions`：会话 UUID、UTC 开始时间、采样率、状态。每次重连/重启产生新会话。
- `recordings`：相对数据目录的 WAV 路径、会话内起止采样位置、关闭标志。
- `transcripts`：最终文字、句段起止采样、UTC 纳秒时间、结束原因。
- `data/audio/<session>/<start_sample>.wav`：默认 48kHz 单声道 16 位 PCM，包括静音。

所有区间均为左闭右开。文字对应的是识别句段，包含端点判断的静音，**不是逐字时间戳**。端点默认尾部静音 1.2/2.4 秒，最长句段约 20 秒。UTC 时间由会话开始时的系统时间加采样时长计算，不受会话中系统时钟跳变影响；音频内定位以采样位置为准。UTC 起点包含设备启动/缓冲延迟，不能视为硬件级精确时间；建议配置 NTP。

音频读取使用独立线程，推理队列约容纳 10 秒。队列拥塞或 ALSA 退出会记录错误并重建会话，不会把中断伪装为连续音频。识别或磁盘写入长期跟不上时需降低线程争用或改用性能更好的设备。捕获错误和服务停止时，队列中尚未处理的数据可能丢失。突然断电或进程崩溃时，最近约一分钟的内存数据可能丢失，只有已写入文件/数据库的内容可恢复；WAV 每次批量写入刷新头部，启动会修复未关闭分段的数据库结束位置。文件与数据库不是跨介质原子事务，硬断电损坏文件可能需要人工修复。

## systemd 常驻部署

将项目及模型放到 `/opt/mic-spy`，先在该目录运行 `uv sync --locked --no-dev`。服务直接使用虚拟环境，运行时不下载依赖。

```sh
sudo useradd --system --no-create-home --groups audio micspy
sudo cp deploy/mic-spy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mic-spy
sudo journalctl -u mic-spy -f
```

`micspy` 需能读取项目、虚拟环境和模型目录；若自定义目录或设备，修改 service 中路径。systemd 自动创建 `/var/lib/mic-spy` 并赋予服务用户写权限。请先以前台方式确认模型可加载及 ALSA 设备可用。

## 容量与边界

原始 PCM 每天约 8.29 GB（7.72 GiB）。默认保留最近 7 天录音，启动时及运行中每小时自动清理过期的已关闭 WAV 分段和关联文字。连续录音约需 58.06 GB（54.1 GiB），部署时仍需预留容量并监控磁盘。磁盘满会报错重试。只有一个采集实例可使用同一数据目录，不能同时运行前台与服务；后续 Web 面板应只读数据库、不要创建 Archive。

本地测试：`uv run pytest`。存储及跨文件回放可在 Mac 验证，实际采集必须在目标 Armbian 上测试。

## 模块划分

| 模块 | 职责 |
| --- | --- |
| `source.py` / `AlsaSource` | 从 ALSA 读取音频，只输出单声道 int16 PCM |
| `recognition.py` / `SherpaRecognizer` | 接收 PCM，输出含采样区间的识别句段，不读写数据库 |
| `storage.py` / `Archive` | 保存 WAV 分段、会话信息与识别文字，不依赖识别器 |
| `query.py` | 只读文字检索、跨文件回放导出，可供后续 Web 面板调用 |
| `transport.py` | FFmpeg 编码发送、UDP 解码来源及进程关闭 |
| `runtime.py` / `run_session` | 串联组件，管理队列和会话关闭 |
| `contracts.py` | 音频来源、识别器、存储接口及 `Transcript` 数据类型 |
| `cli.py` | 解析命令、创建组件、处理信号与重试 |

替换声音来源或识别方案时，实现 `contracts.py` 对应接口并在入口组装即可。`run_session` 不依赖 ALSA 或 sherpa-onnx 的具体实现。音频格式固定为单声道、little-endian int16 PCM，声音来源与识别器采样率须一致；存储与识别器接收同一份音频，所有识别区间从会话采样 0 开始计数。

## 录音自动管理

`run` 和 `processor` 默认启用 7 天保留策略，无需额外服务或计划任务；`collector` 不在本地保存录音，因此不执行清理。可调整保留天数，例如：

```sh
uv run mic-spy --data ./data processor --retention-days 7
uv run mic-spy --data ./data run --retention-days 14
```

启动时检查一次，运行中每小时检查一次，即使正在等待网络音频也会检查。按照数据库记录的录音分段结束时间判断，严格早于“当前 UTC 时间减去保留天数”才删除；跨越截止时间的整段暂时保留，因此清理可延迟约一小时加一个分段长度。正在写入的 WAV 不会被删除。

删除音频时同步删除与该分段重叠的文字，包括跨文件边界的句子；空的历史会话及目录也会清理。文件删除失败会记录日志、保留数据库记录，下一次检查重试。数据库空间可重复利用，SQLite 文件大小不会立即缩小；独立导出的回放文件不在自动清理范围内。`retention.py` 负责清理逻辑。

## 每分钟批量写盘

`run` 和 `processor` 将音频及识别文字先放在内存中，默认每 60 秒集中写入 WAV，并在一个 SQLite 事务中提交这一批的文字与音频索引。期间不会为每个音频块或每句话写盘。运行时即使暂时没有音频，也会检查是否到达保存时间；正常停止或捕获异常退出会立即保存剩余缓存。首次启动的数据库初始化、会话创建和每小时的过期清理仍会写盘。

```sh
uv run mic-spy --data ./data processor --flush-seconds 60
```

48kHz 下每分钟音频缓存约 5.76 MB；查询只能看到已提交的数据，因此识别文字在查询中可能延迟约一分钟出现。WAV 默认仍按 5 分钟分段，保存间隔与文件分段长度互相独立。每批音频写入后刷新并同步文件，再提交 SQLite；操作系统实际物理写入时间由其文件系统管理。突然断电或强制结束进程会丢失尚未保存的内存缓存。

## Web 面板与 HTTP API

前端使用 React + Vite（Node.js 20.19+ 或 22.12+），后端使用 FastAPI。提供关键词检索、时间筛选、分页、录音回放与下载、档案统计，每分钟自动刷新。界面显示浏览器本地时区；查询范围按句段与时间区间是否重叠匹配。

在处理端构建并启动：

```sh
uv sync --locked --no-dev
cd web
npm ci
npm run build
cd ..
uv run mic-spy --data ./data serve --host 0.0.0.0 --port 8000
```

浏览器打开 `http://处理设备IP:8000`。录音处理器 `processor` 和 Web 服务 `serve` 是独立进程，需同时运行，且 `--data` 指向同一目录。Web 服务不启动录音，不创建数据库；处理器尚未建立数据库时页面会提示等待数据。单独运行 `serve` 默认只监听 `127.0.0.1`，上述 `0.0.0.0` 用于局域网访问。当前没有登录认证，请部署在受信任的局域网。

生产运行不需要 Node.js 常驻：FastAPI 同时提供 `web/dist` 静态文件与 `/api` 接口。可用 `--web-dist /绝对路径/web/dist` 指定前端目录。常驻配置见 `deploy/mic-spy-web.service`，按前述步骤安装并启用；服务用户需要读取处理端数据库、SQLite WAL 和音频文件。

前端开发时，先启动后端，再在 `web` 目录运行 `npm run dev`，打开终端显示的地址。Vite 将 `/api` 请求代理到 `http://127.0.0.1:8000`。

| HTTP 接口 | 功能 |
| --- | --- |
| `GET /api/health` | 服务与数据库是否存在 |
| `GET /api/stats` | 句段数量、音频时长、最早与最新记录 |
| `GET /api/transcripts` | 支持 `text`、`start`、`end`、`session_id`、`limit`（1–100）、`offset` |
| `GET /api/transcripts/{id}/audio` | 对应 WAV，可跨文件边界，支持字节范围请求与浏览器拖动回放 |
| `GET /docs` | 交互式 API 文档 |

`start` / `end` 接受带时区的 ISO 8601 时间；纳秒字段以字符串输出，避免 JavaScript 精度损失。音频回放在内存中生成，不新增磁盘文件；过期或不完整音频返回 410，缺失文字返回 404。单次回放上限 10 分钟，超过时用命令行导出。只显示已提交的文字与音频，批量写盘带来的延迟约一分钟。

## 48 kHz 与启动模式

`collector`、`run`、`processor` 默认使用 48000 Hz、单声道、16 位 PCM。`--sample-rate 16000` 可切换回旧采样率。网络两端建议采用相同采样率；处理端会让 FFmpeg 将输入流转换为指定的录音采样率。Opus 为有损编码，采样率提高不等于编码后恢复全部原始音质，可根据需要调整 `--bitrate`。

识别器的模型特征采样率仍固定为 16000 Hz，向 sherpa-onnx 输入时携带真实采样率，由其内部重采样；文字采样位置使用录音采样率，回放定位保持一致。已有 16kHz 会话继续按数据库中的采样率回放，无需迁移。

| 命令 | 启动功能 |
| --- | --- |
| `collector` | 麦克风采集 + 编码 + UDP 发送，不需要模型 |
| `processor` | UDP 接收 + 识别 + 录音与文字存储 |
| `run` | 本机麦克风采集 + 识别 + 录音与文字存储 |
| `serve` | Web 面板 + HTTP API |
| `full --source local` | 本机采集 + 识别 + 存储 + Web/API |
| `full --source udp` | UDP 接收 + 识别 + 存储 + Web/API |

先按 Web 章节构建前端，然后一条命令启动全功能：

```sh
# 同一台设备运行全部功能
uv run mic-spy --data ./data full --source local \
  --device plughw:0,0 --sample-rate 48000 --host 0.0.0.0 \
  --model /模型目录/model.int8.onnx --tokens /模型目录/tokens.txt

# 处理设备：接收音频并同时提供 Web
uv run mic-spy --data ./data full --source udp \
  --listen udp://0.0.0.0:5000 --sample-rate 48000 --host 0.0.0.0 \
  --model /模型目录/model.int8.onnx --tokens /模型目录/tokens.txt

# 采集设备：仅发送
uv run mic-spy collector --device plughw:0,0 \
  --sample-rate 48000 --target udp://处理设备IP:5000
```

`full` 默认使用本机麦克风、Web 端口 8000；可用 `--host`、`--port`、`--web-dist` 配置。Ctrl+C/SIGTERM 会停止录音并保存缓存，同时关闭 Web 子进程。Web 异常退出时全功能模式会停止并返回失败状态，方便 systemd 重启。运行 `full` 时不要再针对同一目录启动 `run` / `processor`，也不要在同一端口另外启动 `serve`。
