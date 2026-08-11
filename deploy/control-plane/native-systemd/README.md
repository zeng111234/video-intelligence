# 原生 systemd 安全发布路径

这组脚本只允许操作以下对象：

- `/opt/videoinsight-control-plane`
- `/etc/systemd/system/videoinsight-control-plane.service`
- `videoinsight-control-plane.service`

它不会调用 Docker、Compose、Caddy、Nginx，也不会启停或修改任何其他服务。反向代理和证书继续由服务器现有系统管理，本目录不负责它们。

正式付费验收依赖隔离在控制层目录内的 `ffprobe` 和 `ffmpeg`。发布脚本只读检查它们，不会安装、下载或修改系统全局媒体工具。新 unit 把服务 `PATH` 固定为 `/opt/videoinsight-control-plane/tools/media/bin:/usr/local/bin:/usr/bin:/bin`，并要求两个名称都精确解析到隔离目录，避免 root 登录环境、系统全局目录与 systemd 服务环境不一致。

## 固定目录

```text
/opt/videoinsight-control-plane/
  config/control-plane.env
  incoming/VideoInsight-control-plane-<版本>.zip
  incoming/cpython-3.12.13+20260807-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
  python/3.12.13/bin/python3.12
  python/3.12.13.legacy-pyc-drift-3f3407b97c487aaf0dedd632590fd7731486675cf77000827918274bab07b8b0/
  wheelhouse/*.whl
  tools/native-systemd/wheelhouse.sha256
  tools/native-systemd/media-tools.sha256
  tools/native-systemd/offline-python-archive.sha256
  tools/native-systemd/offline-python-legacy-drift-tree.sha256
  tools/native-systemd/offline-python-tree.sha256
  tools/media/bin/ffmpeg
  tools/media/bin/ffprobe
  releases/<版本>/app/
  releases/<版本>/venv/
  current -> releases/<版本>
  runtime/data/
  backups/
  state/offline-python-runtime-repair
  state/
```

`config/control-plane.env` 必须是 `root:videoinsight` 且权限 `0640`；`runtime` 父目录必须是 `root:root` 且组/其他用户不可写，只有 `runtime/data` 属于显式服务 UID/GID 且权限 `0700`；`backups` 必须是 `root:root` 且权限 `0700`。`preflight.sh` 和 `verify.sh` 会在同一文件系统内递归检查 `runtime/data`：每层目录和每个普通文件都必须属于显式服务 UID/GID，组/其他用户权限位必须为零，并且设备号必须与 data 根一致。任一符号链接、子挂载、跨文件系统项、设备、错误属主或宽松权限都会失败；备份和恢复删除旧目录前也会独立做同样的 fail-closed 检查。服务进程不需要写备份目录，停机快照和恢复只由 root 发布脚本执行。

`ffprobe` 和 `ffmpeg` 必须由管理员在启用服务前取得并核验，再放入 `/opt/videoinsight-control-plane/tools/media/bin`；服务进程和发布脚本不得联网安装。为避免客户电脑上行流量，首次准备允许管理员把固定来源归档直接下载到本服务自己的 `incoming` 目录，先核对供应商 MD5 和归档 SHA256，再提取两个受审文件；不得放入 `/usr/local/bin` 或 `/usr/bin`，也不得猜测包版本。建议使用不依赖额外私有动态库的已审查 Linux x86_64 构建。二进制必须是两个直接普通文件，不能是符号链接；固定目录不得包含任何额外条目。

开发电脑必须先对两个最终二进制计算 SHA256，再把小写哈希写入随源码跟踪的 `media-tools.sha256`。服务器只能核对这个受审清单，禁止上传后在服务器重新生成“期望哈希”。当前受审来源是 `https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz`，归档名 `ffmpeg-release-amd64-static.tar.xz`，FFmpeg 版本 `7.0.2`，归档 SHA256 `abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67`，供应商 MD5 `7fa72b652e19bf84c9461e332ea1cdf3`，许可为 GPLv3。归档内最终 `ffmpeg` 与 `ffprobe` 的固定 SHA256 以本目录的清单为唯一运行门禁；任何版本或哈希变化都必须作为新的受审发布变更处理。

二进制、隔离目录及其到 `/` 的每层祖先必须由 `root` 持有，且组/其他用户不可写；组只允许 `root` 或固定 `videoinsight` 组。`root:root` 条目必须给其他用户执行/遍历权限，`root:videoinsight` 条目必须给固定服务组执行/遍历权限，因此控制层根目录可以安全使用 `root:videoinsight 0750`；其下 `tools`、`media` 和 `bin` 仍必须是 `root:root`。`preflight.sh` 在解包、停机和任何付费动作前检查文件集合、路径、属主、权限和 SHA256，同时逐个确认 `/usr/bin/env`、`/usr/bin/timeout`、`/usr/sbin/runuser` 是 root 持有、组/其他不可写、可执行且不经过符号链接的固定系统文件。随后以固定绝对路径启动 10 秒硬超时，并用 `runuser` 以固定 `videoinsight:videoinsight` 身份执行两个精确候选的 `-hide_banner -version`；外层和工具子进程都通过 `/usr/bin/env -i` 清空环境，只重新设置固定 PATH 和不可用 HOME，不继承代理、登录 HOME、`LD_*` 或其他 root 环境，也不优先信任 `/usr/local`。`verify.sh` 会再独立完成同一组依赖、哈希和服务身份执行检查并记录；缺失、额外文件、符号链接、路径逃逸、错误属主、宽松权限、哈希变化、ABI/执行失败、超时或服务不可达都会 fail-closed。上传发布包前应在目标 CentOS 7 主机执行并保留以下只读证据：

```bash
PATH=/opt/videoinsight-control-plane/tools/media/bin:/usr/local/bin:/usr/bin:/bin command -v ffprobe ffmpeg
readlink -f /opt/videoinsight-control-plane/tools/media/bin/ffprobe
readlink -f /opt/videoinsight-control-plane/tools/media/bin/ffmpeg
/bin/bash /opt/videoinsight-control-plane/tools/native-systemd/preflight.sh 996 994
```

新 unit 使用 `current/app` 作为工作目录，并通过 `current/venv/bin/python -m uvicorn` 启动。这样代码与依赖属于同一个不可覆盖的版本，避免“新代码配旧 venv”的混跑。

## 首次从旧布局迁移并升级

不要先手工改 `current`，也不要直接用 `upgrade.sh` 接受服务器遗留的宽松 unit。真实旧基线是一个精确的混合组合：`current` 绝对链接指向 `0.2.6/app`，而唯一解释器来自 `0.2.4/venv/bin/python` 并解析到固定离线 Python。历史安装在解包后直接用 root 运行了 base Python 和 `uv venv`，因此运行时自然增加了 198 个 `*.pyc` 与 23 个 `__pycache__` 目录；这棵 pyc-drift 树只能作为被替换源的取证绑定，不能成为 Python 执行信任。必须先运行一次性的 `normalize_offline_python_runtime.sh` 原子换成官方 clean runtime，再运行 `normalize_legacy_unit.sh` 把旧 application/interpreter 收养为 strict bridge。两个脚本都不是通用导入器，也不接受版本范围或其他摘要。

官方 clean runtime 的权威资产是 `astral-sh/python-build-standalone` GitHub tag `20260807` 下的精确文件 `cpython-3.12.13+20260807-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`；历史实际下载 URL 是 `https://releases.astral.sh/github/python-build-standalone/releases/download/20260807/cpython-3.12.13%2B20260807-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`，大小 `34163738` 字节，SHA256 `506191be3ee7bd190a8834dcdc1b3bc70aab50608deccc711935aa007239cabd`。`offline-python-archive.sha256` 同时固定文件名、大小、历史 URL 与哈希；脚本只接受上述固定 `incoming` 绝对路径、root 持有的普通非链接文件，不会下载归档。

可复现解包配方是：固定 `/usr/bin/tar`、`umask 022`、`--strip-components=1 --no-same-owner --no-same-permissions` 解到 Python 父目录同一文件系统、由 root 持有的 stage，再 `chown -hR root:root`，将根和所有目录显式规范为 `0755`，并对全部普通文件移除 group/other write（归档的 `0664/0775` 因而成为 `0644/0755`）。只有在纯系统工具 tree-v1 门禁重算为 `4728` 个后代、`3485` 个普通文件、`1048` 个内部符号链接及 SHA256 `f4446ac8e57f0a85d2bd0851fc05acb0cd5e8137f6752df428116ddc8e4fab01` 后，脚本才允许执行或启动新 Python。

1. 只把正式构建生成的 ZIP 上传到固定 `incoming` 路径，属主设为 `root:root`，且组和其他用户不可写。
2. 第一次迁移时，把同一已验证工作区中的 `deploy/control-plane/native-systemd` 目录单独打成工具包，并上传到固定的 `/opt/videoinsight-control-plane/tools/native-systemd`。该目录只含脚本、校验器、wheelhouse 哈希清单、unit 和说明，不含密钥；服务器上设为 `root:root`，目录不可由组或其他用户写入，并保留仓库中的 LF 换行。旧版 `current/app` 尚无这些工具，不能假装从那里执行。
3. 在开发电脑记录工具包 SHA256，上传后只计算一次服务器文件的 SHA256 并人工核对；不一致就停止，不要在服务器重新打包。`upgrade.sh` 还会在停机事务前逐文件比较外置工具目录与新 ZIP 内的 `deploy/control-plane/native-systemd`，文件缺失、额外文件或任一内容不同都会拒绝升级。
4. 在开发电脑记录部署 ZIP 的 SHA256；不要在服务器上重新生成“期望哈希”。
5. 在服务器只读查询服务 UID/GID：

   ```bash
   id -u videoinsight
   id -g videoinsight
   ```

6. 把上述官方 Python 归档放到固定 `incoming` 路径并核对本机记录的大小/SHA256。执行任何 shipped shell 前，先在目标服务器用系统 `/bin/bash -n` 解析全部 8 个脚本。解析通过后先修复离线 Python；修复完成后再次执行同一 Bash 4.2 解析门禁，再进行 legacy adoption。下面命令中的版本、链接、文件数和所有 SHA256 都是已审计固定值；只能整条原样使用，服务 UID/GID 仍须与 `id` 输出核对：

   ```bash
   cd /opt/videoinsight-control-plane/tools/native-systemd || exit 1
   TARGET_VERSION='<正式构建生成的版本>'
   for script in common.sh install_unit.sh normalize_offline_python_runtime.sh \
     normalize_legacy_unit.sh preflight.sh \
     rollback.sh upgrade.sh verify.sh; do
     /bin/bash -n "$script" || exit 1
   done
   /bin/bash normalize_offline_python_runtime.sh 996 994
   for script in common.sh install_unit.sh normalize_offline_python_runtime.sh \
     normalize_legacy_unit.sh preflight.sh \
     rollback.sh upgrade.sh verify.sh; do
     /bin/bash -n "$script" || exit 1
   done
   /bin/bash normalize_legacy_unit.sh \
     0.2.6 0.2.4 /opt/videoinsight-control-plane/releases/0.2.6/app \
     98e7841399dcb1cb5654225bbfde65735fe0dc8cc3b56c0bd2336ad6f116a994 \
     839ad0602fd054d3d3d2eb5574c6184d4e6ceafa2d381d06f7a7a8dffe6aaf84 \
     021044895e95be79dc2f110367607e684119afbc8ce75f6f0eec94844e0acec7 \
     175 81c846d367b74d087fd845372f673a78011cdd0f48f2952c91a98f7e22ab2dc6 \
     1594 bfd8ba051af78d812c9b39c1679b7843c14196cea77ee7457b8599e8797368da \
     996 994
   /bin/bash preflight.sh 996 994
   /bin/bash upgrade.sh "$TARGET_VERSION" '<开发电脑记录的 SHA256>' 996 994
   /bin/bash verify.sh "$TARGET_VERSION" 996 994
   ```

服务器必须在执行任何 shipped shell 前，用系统 `/bin/bash -n` 对上述 8 个文件逐个解析；任一文件失败就停止，不得继续 Python repair、legacy normalize、preflight、upgrade、rollback 或 verify。离线 Python 修复成功后还要在 legacy normalize 前重复这道门禁，明确顺序为“repair 成功 → 服务器 Bash 4.2 全量解析 → legacy normalize”。开发机较新的 Bash 通过不能代替目标服务器解析通过。

`normalize_offline_python_runtime.sh` 与其他发布脚本竞争同一个 `native-release.lock`。首次迁移时它必须在 `normalize_legacy_unit.sh` 之前执行；完成迁移后不得把它当作日常命令重复运行。若已存在精确 active 修复描述，但受保护 runtime 后续因意外缓存写入而不再命中 clean tree，脚本只允许在已安装 unit 精确等于受审模板、`current` 精确指向一个版本化发布目录且该目录的版本、venv 和 effective unit 全部通过门禁时执行一次 active refresh；污染树会原子移到固定取证目录，已有取证目录时拒绝覆盖。脚本取得锁后、清理临时文件或调用任何 `systemctl stop` 前，先以纯系统工具把磁盘 unit 精确绑定到受审原 SHA256 `98e7841399dcb1cb5654225bbfde65735fe0dc8cc3b56c0bd2336ad6f116a994`（恢复路径也只接受已审计 strict bridge 或精确标准发布 unit），并逐项核对 FragmentPath、无 drop-in、`NeedDaemonReload=no`、WorkingDirectory、User/Group、effective Environment 与完整 ExecStart；unit 不能绑定时会直接退出，不会触发未知 ExecStop/ExecStopPost。它在服务仍保持原状时先完成归档、成员、权限和 clean tree 门禁，再持久化固定 `prepared` 描述，随后只停止并确认 `videoinsight-control-plane.service`，把旧 runtime 与 clean stage 依次在同一 Python 父目录内原子 rename。clean runtime 通过门禁后，启动前还会以 `-B` 的受控 Python重算固定 application `175 / 81c846d367b74d087fd845372f673a78011cdd0f48f2952c91a98f7e22ab2dc6` 与 interpreter `1594 / bfd8ba051af78d812c9b39c1679b7843c14196cea77ee7457b8599e8797368da` 证据；strict bridge 则必须有完整 active adoption 证据。每次启动与健康检查后都会清除进程内 Python 门禁缓存并重新计算 clean tree，确认服务没有生成 `*.pyc` 或改变 runtime 后才原子晋升 `active` 描述。成功时旧 pyc-drift runtime 仍以固定摘要命名保留。断电重跑只接受 initial、old 已备份、clean 已切换或 active 四种精确组合；其他混合状态只在 unit 可安全绑定时停止本服务，无法绑定时则不调用 stop 并输出 CRITICAL。切换失败时只恢复旧目录布局和 clean stage 供重跑，绝不会启动或把 legacy drift 树标记为可信 Python；恢复本身无法无歧义完成时同样保持固定服务停止。

`normalize_legacy_unit.sh` 在任何替换前同时核对原 current 文本、原 unit 哈希、离线 Python 哈希，以及 application/interpreter 两棵树从根开始的类型、POSIX 相对路径、权限、UID/GID、文件内容或符号链接原始目标。两树及版本祖先必须同设备、无独立挂载、root 持有且不可由组或其他用户修改；固定服务身份必须能遍历目录和读取文件。application 禁止符号链接，interpreter 只允许审计固定的 4 个链接。原宽松 unit 使用单一 `O_NOFOLLOW` 源文件描述符与 `O_EXCL` no-clobber 目标保存在 `state/legacy-adoption/`，只作取证；生成的 bridge 则完整包含固定 User/Group、媒体 PATH、`CPUQuota=100%`、`MemoryLimit=1G` 和其他 strict 指令。

在 bridge 替换前，脚本会先持久化 `status=prepared` 描述；若断电发生在 unit rename 后、active 发布前，重跑只接受精确的 original+prepared 或 bridge+prepared 组合并继续，其他混合或篡改状态只停止本服务并 fail closed。成功后原子晋升为 `state/legacy-adoption.json` 的 `active` 状态；完整 active 重跑是幂等操作。失败时只操作本服务，若恢复或健康检查失败就保持停止。

任何 root 级 Python 调用前都会先用固定系统工具核对官方 clean `offline-python-tree.sha256`：精确 4728 个后代、3485 个普通文件、1048 个内部符号链接及上述 tree-v1 聚合 SHA256。运行时、祖先和全部非链接条目必须 root 持有、组和其他用户不可写、同设备且无子挂载；链接不得失效或越界。若当前树仍精确命中 `offline-python-legacy-drift-tree.sha256`，`preflight.sh`、`normalize_legacy_unit.sh` 和其他发布入口都会明确要求先运行 runtime repair，且不会设置 Python validated 标志。clean 门禁通过后，脚本只通过清空环境的 wrapper，以 `-B -I -S -X utf8` 执行固定离线 Python；staging venv 的 pip/check/import 是唯一保留 site-packages 的路径，但仍使用空环境、`-B -I -X utf8`、固定 TMPDIR、no-index 和哈希锁。

`upgrade.sh` 只接受 `x.y.z` 稳定版本，并在解包或停机前要求新版本严格高于当前版本；同版重放和降级都必须改用下面的受控 `rollback.sh`，不能把旧 ZIP 改名冒充新版本。它还会拒绝已存在的版本目录、错误哈希、包内版本标识不一致、不安全 ZIP、非离线依赖、UID/GID 不一致、非固定 unit、越界符号链接和并发发布。ZIP 内每个普通成员都会分块扫描私钥/PuTTY 标记，跨分块标记也会命中，不会因为单个成员超过 32 MiB 而跳过。legacy 只接受 active adoption 描述绑定的 strict bridge，不再解析或放宽原 unit。依赖安装固定在版本暂存目录内（包括 `TMPDIR`），禁用用户 site、pip 配置和缓存，强制 `--no-index --only-binary=:all: --require-hashes` 使用包内 `requirements.lock`；`preflight.sh` 同时核对受跟踪的 `wheelhouse.sha256` 与 wheelhouse 的精确文件集合和每个 SHA256，因此不能通过替换同名 wheel 或临时塞包改变依赖。全程不会联网下载。

发布目录树在进入 `releases/<版本>` 前会同步全部普通文件和目录；`release`、`current`、unit、unit 备份和 legacy 描述的关键 rename 都在前后同步固定父目录。任何提交边界的落盘失败都会被事务视为失败并进入恢复路径，不会在健康检查通过后才把未落盘的元数据当成成功。

健康检查只请求 `127.0.0.1:18080`，并从受保护的 `control-plane.env` 读取域名作为 Host；curl 显式使用 `--disable --noproxy '*'`，不会受 root 的 `.curlrc` 或代理环境影响。每个健康阶段只有初次检查和一次重试。它不会调用真实供应商。

## 自动回滚

升级停机后使用本次已校验的新 release 中的备份脚本生成停机快照，并在清单写入源版本；不会调用 legacy/旧 release 的备份或恢复脚本。备份通过 data 根目录文件描述符逐层 `O_NOFOLLOW` 打开附加文件，SQLite Online Backup 在 Linux 上也只从已固定并复核 inode 的 `/proc/self/fd/<fd>` 打开源库，不会在校验后重新信任原数据库路径；目标 ZIP 用原子 no-clobber 链接创建，并发同名文件只会让备份失败，不会被覆盖。ZIP 在返回成功前会同步归档文件和备份父目录。新版本、unit 或健康检查任一步失败时，新 release 的恢复脚本会先在 `runtime` 同一文件系统内构建完整候选数据目录，完成 manifest 和数据库完整性检查；候选根保持 `root:root`，所有后代只经由 `dirfd`、`O_NOFOLLOW`、`fstat`、`fchmod`、`fchown`、`fsync` 处理，最后才移交候选根所有权，随后不再按路径递归候选树而直接执行同文件系统目录切换。目录切换各阶段也同步 `runtime` 父目录。复制、空间、权限或提交前落盘错误发生时原 `runtime/data` 保持原样，候选不会成为服务数据。候选和父目录成功落盘即为提交点；提交后的旧目录删除或最终父目录落盘若异常，会保持完整的新 `runtime/data` 并输出 `WARNING`，不会谎报成“失败但已换数据”。随后脚本会：

1. 只停止 `videoinsight-control-plane.service`；
2. 恢复停机数据快照；
3. 原样恢复升级前的 `current` 链接（包括旧的 `.../app` 布局）；
4. 恢复升级前的 unit 并执行 `daemon-reload`；
5. 启动旧控制层并进行初次检查加一次重试。

若数据、链接或 unit 任一恢复失败，脚本会保持本服务停止并输出 `CRITICAL`，不会带着不完整状态继续运行。恢复数据库会按现有安全策略注销旧会话。

## 成功发布后的人工回滚

仅在确认目标旧版本目录及其“升级前停机快照”同时存在时执行：

```bash
/bin/bash rollback.sh 0.2.7 'videoinsight-control-plane-pre-upgrade-0.2.8-时间-PID.zip' 996 994
```

`rollback.sh` 会先为当前版本再做一份安全快照；若旧版本恢复失败，会自动恢复回滚前代码和数据。新布局目标还必须在停机前通过版本、venv 以及 native 工具完整内容比对，避免回滚成功后无法再运行固定验证工具。不要手工删除或覆盖 `releases` 内的版本。版本内容错误时使用新的版本号向前修复。

### 首次迁移后回 legacy 版本

首次从 active adoption legacy 布局升级成功后，`upgrade.sh` 会在 `state/legacy-rollbacks/<停机快照名>.json` 保存 `root:root 0600` 的 v2 描述，精确绑定 active adoption 文件及其 SHA256、`0.2.6` application、`0.2.4` interpreter、原 current、strict bridge 备份及停机快照。保留升级输出中的“legacy 人工回滚描述”路径。需要回 legacy 时仍使用同一条四参数命令，例如：

```bash
/bin/bash rollback.sh 0.2.6 'videoinsight-control-plane-pre-upgrade-0.2.7-时间-PID.zip' 996 994
```

目标没有新布局时，脚本只接受上述 adoption v2 描述，并再次计算 active adoption、快照和 strict bridge 备份哈希。它使用当前新版本的备份/恢复工具恢复目标数据，再恢复描述绑定的 legacy current 与 strict bridge；永远不会恢复原宽松 unit，也不会执行目标旧版脚本或复制/猜测旧 venv。若 legacy 启动失败，会恢复回滚前的新 unit、current 和安全快照并确认新版本健康；恢复链任一步失败则保持本服务停止。

## 单独安装 unit

`install_unit.sh` 只适用于 `current -> releases/<版本>` 且该版本已有 `app` 与 `venv` 的新布局。它会备份旧 unit、原子替换并执行 `daemon-reload`，但不会重启任何服务。旧布局迁移必须使用 `upgrade.sh`，不能用此脚本跳过事务。

## 仍需人工保留的证据

- 上传前 ZIP 的本机 SHA256 与文件大小；
- `upgrade.sh` 输出的停机快照、旧 unit 备份路径；
- 首次迁移时工具包的本机/服务器 SHA256，以及 `legacy 人工回滚描述` 路径；
- `verify.sh` 的通过输出；
- 反向代理侧的外部 HTTPS 验收由独立步骤完成，本脚本不碰现有站点配置；
- wheelhouse、`wheelhouse.sha256` 和工具目录必须继续保持 `root:root` 且不可由组或其他用户写入。更换依赖时应根据完整的新 wheelhouse 重新生成并审查 `requirements.txt`、`requirements.lock` 与 `wheelhouse.sha256`，不能往旧目录临时塞包。
