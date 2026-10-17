# UE5 自动化合成数据渲染管线架构图

这份文档展示了基于自定义 `MatrixCityPlugin` 插件的 UE5 自动化多模态数据生成管线架构。当前实现采用**双进程架构**，并通过 **Socket 进行进程间通信 (IPC)**，用于状态同步、进度日志传递和任务收尾控制。

---

## 核心架构流程图

下面的 Mermaid 序列图展示了整个工作流的详细交互，重点突出了 OS 层面的控制脚本与 UE5 内部执行环境之间的职责分离，以及关键的 Socket 通信机制。

```mermaid
sequenceDiagram
    autonumber
    participant OS as OS / 外部 Python 总控脚本<br/>(run_cmd_async.py)
    participant Socket as Socket Server<br/>(127.0.0.1:9999)
    participant UE5 as Unreal Engine 5 进程
    participant Pipeline as UE 内部 Python 插件<br/>(pipeline.py)
    participant Storage as 文件系统 / 存储

    %% 阶段 1：初始化与启动
    rect rgb(240, 248, 255)
        Note over OS, Storage: 阶段 1：环境初始化与进程拉起
        OS->>OS: 解析配置 (user.json, render_config.yaml)
        OS->>Socket: 启动本地 Socket 服务器 (进入“看门狗”模式)
        OS->>UE5: 通过命令行无头(Headless)拉起 UE5 引擎实例
        Note right of OS: OS 级 Python 脚本开始异步监控 UE5 进程状态
    end

    %% 阶段 2：UE5 内部加载与建立通信
    rect rgb(255, 245, 238)
        Note over OS, Storage: 阶段 2：引擎加载与 Socket 握手建立通信
        UE5->>Pipeline: 引擎启动后通过命令行 ExecCmds 执行 pipeline.py
        Pipeline->>Socket: 发起连接 (Connect to Socket Server)
        Pipeline->>Socket: 发送状态信号 "Unreal Engine Loaded!"
        Socket-->>OS: 转发状态至总控脚本
        OS->>OS: 更新内部状态 (标记 unreal_loaded = True)
    end

    %% 阶段 3：自动化渲染循环与监控
    rect rgb(240, 255, 240)
        Note over OS, Storage: 阶段 3：全自动相机轨迹生成与批量渲染监控
        Pipeline->>Pipeline: 自动生成相机轨迹 (utils_sequencer.py)
        Pipeline->>Pipeline: 配置 MRQ (Movie Render Queue)<br/>设定输出通道: RGB, Depth, Normal 等
        
        loop 渲染队列执行 (按目标物 / 轨迹序列)
            Pipeline->>UE5: 触发渲染任务
            UE5->>Storage: 输出渲染结果 (EXR, PNG)
            Pipeline->>Socket: 汇报进度: 目标物处理、轨迹生成、渲染步骤开始/完成
            Socket-->>OS: 转发进度至总控脚本
            
            %% 异常处理 / 崩溃恢复机制
            alt 如果检测到 UE5 崩溃 (无响应或进程意外退出)
                OS->>OS: 检测到进程退出/CrashReportClient，记录异常并结束当前轮次
                OS->>UE5: 清理残留进程（若存在）
                Note right of OS: 当前实现不包含自动断点续传重启逻辑
            end
        end
    end

    %% 阶段 4：可选后处理与数据校准
    rect rgb(255, 250, 205)
        Note over OS, Storage: 阶段 4：渲染结束后的可选后处理（独立脚本）
        Storage-->>Storage: 离线/后处理输出数据
        Note right of Storage: 例如：位姿/坐标系修正、深度可视化、点云转换等脚本化处理
    end

    %% 阶段 5：清理与退出
    rect rgb(245, 245, 245)
        Note over OS, Storage: 阶段 5：任务完成与资源清理
        Pipeline->>Socket: 发送 "Pipeline Finished" 信号
        Socket-->>OS: 转发状态至总控脚本
        Pipeline->>UE5: 调用 quit_editor() 退出编辑器
        OS->>OS: 收到完成信号后结束监控循环并退出
    end
```

---

## PPT / 论文风格流程图（专业简化版）

下面这张图用于展示方法主干：输入定义、管线编排、场景迭代与多模态输出，适合直接用于答辩或论文方法页。

```mermaid
flowchart LR
    %% Inputs
    S1[输入占位：UE5 场景列表]:::io
    S2[输入占位：相机 / 轨迹配置]:::io
    S3[输入占位：渲染参数配置]:::io

    %% Swimlane 1: External Orchestrator
    subgraph L1[外部会话编排器（Session Orchestrator / OS）]
        O1[配置解析与任务初始化]:::core
        O2[启动 UE 会话并建立 Socket 服务]:::core
        O3{当前场景任务完成?}:::decision
        O4{存在下一场景?}:::decision
        O5[切换到下一场景任务并重启会话]:::core
        O6[管线收尾与退出]:::core

        O1 --> O2 --> O3
        O3 -- No --> O3
        O3 -- Yes --> O4
        O4 -- Yes --> O5 --> O2
        O4 -- No --> O6
    end

    %% Swimlane 2: In-Engine Worker
    subgraph L2[引擎内渲染执行器（Render Pipeline Worker / UE）]
        U1[加载当前场景上下文]:::core
        U2[轨迹构建 / 轨迹读取]:::core
        U3[MRQ 队列编排]:::core
        U4[渲染执行（RGB / Depth / Pose）]:::core
        U5[状态与进度回传（Socket）]:::core

        U1 --> U2 --> U3 --> U4 --> U5
    end

    %% Input bindings
    S2 --> O1
    S3 --> O1
    S1 --> U1

    %% Cross-lane interactions
    O2 --> U1
    U5 --> O3

    %% Outputs
    U4 --> R1[输出占位：RGB]:::out
    U4 --> R2[输出占位：Depth]:::out
    U4 --> R3[输出占位：Pose]:::out

    classDef io fill:#E8F1FF,stroke:#4B77BE,stroke-width:1px,color:#111;
    classDef core fill:#EEF7EE,stroke:#4A8F4A,stroke-width:1px,color:#111;
    classDef decision fill:#FFF4E5,stroke:#C27C2C,stroke-width:1px,color:#111;
    classDef out fill:#F2ECFF,stroke:#7B61B3,stroke-width:1px,color:#111;
```

---

## PPT 演讲重点提炼：为什么要用 Socket 通信？

在向面试官展示这个流程图时，重点强调**双进程架构与 Socket 通信带来的工程价值**（按当前实现表述）：

1. **解耦与稳定性 (Decoupling & Robustness):**
    *   外部脚本 `run_cmd_async.py` 独立负责进程拉起、Socket 服务和日志监控，渲染逻辑保持在 UE 内部 Python 侧。
    *   当 UE 进程异常退出时，外部脚本可检测并结束当前任务，避免控制逻辑与引擎生命周期强耦合。

2. **实时状态感知 (Real-time State Monitoring):**
   *   两个进程之间如何对话？**Socket 就是它们的桥梁**。
    *   UE5 内部 `pipeline.py` / `custom_movie_pipeline.py` 会通过 Socket 上报关键状态（引擎加载完成、目标物处理进度、渲染开始/完成、管线结束）。
    *   外部脚本据此做状态判定与日志聚合；当前版本已实现异常检测与进程清理，但未实现自动断点续渲。

3. **数据后处理层（按代码实情）：**
    *   渲染结果可在 `scripts/` 中通过离线脚本进行坐标系修正、深度可视化、点云转换等处理；这部分不在主渲染管线内自动执行。