# UE5 自动化合成数据渲染管线架构图

这份文档详细展示了基于自定义 `MatrixCityPlugin` 插件的 UE5 自动化多模态数据生成管线架构。核心设计采用了**双进程架构**，并通过 **Socket 进行进程间通信 (IPC)**，确保了 CV (计算机视觉) 任务中数据生成的鲁棒性、自动化和高精度。

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
        UE5->>Pipeline: 引擎启动，自动加载并执行 pipeline.py
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
        
        loop 渲染队列执行 (Frame by Frame / Sequence)
            Pipeline->>UE5: 触发渲染任务
            UE5->>Storage: 输出渲染结果 (EXR, PNG) 与位姿 JSON
            Pipeline->>Socket: 汇报进度: "当前帧渲染完成" / "进度 50%"
            Socket-->>OS: 转发进度至总控脚本
            
            %% 异常处理 / 崩溃恢复机制
            alt 如果检测到 UE5 崩溃 (无响应或进程意外退出)
                OS->>OS: asyncio 看门狗检测到异常断开或超时
                OS->>UE5: 强制杀死(Kill)僵尸进程，清理内存
                OS->>OS: 重新执行【阶段 1】(从断点处恢复渲染)
            end
        end
    end

    %% 阶段 4：数据校准与输出 (面试核心高光)
    rect rgb(255, 250, 205)
        Note over OS, Storage: 阶段 4：针对 CV 算法的数据校准 (Fixed_Track 核心贡献)
        Storage-->>Storage: 离线/后处理输出数据
        Note right of Storage: ⭐ 你的核心贡献 (Fixed_Track 分支):<br/>- GT 位姿校准: 修复 UE 左手系到 CV 右手系的旋转矩阵/欧拉角偏差<br/>- 深度图重构: 修正深度切片，对齐 Swin / Occ 神经网络的输入格式
    end

    %% 阶段 5：清理与退出
    rect rgb(245, 245, 245)
        Note over OS, Storage: 阶段 5：任务完成与资源清理
        Pipeline->>Socket: 发送 "All Jobs Finished" 信号
        Socket-->>OS: 转发状态至总控脚本
        OS->>UE5: 发送优雅退出指令 (Graceful Shutdown)
        OS->>Socket: 关闭 Socket 服务器
        OS->>OS: 退出外部 Python 总控脚本，释放所有资源
    end
```

---

## PPT 演讲重点提炼：为什么要用 Socket 通信？

在向面试官展示这个流程图时，重点强调**双进程架构与 Socket 通信带来的工程价值**：

1. **解耦与稳定性 (Decoupling & Robustness):**
   *   UE5 作为一个庞大的 C++ 游戏引擎，在长时间、高负荷进行离线批量渲染时，难免会出现内存泄漏或偶发性崩溃（Crash）。
   *   如果把控制逻辑全写在 UE5 内部，引擎一崩溃，整个渲染任务就彻底挂了。
   *   通过分离出一个独立的 OS 级别 Python 脚本（`run_cmd_async.py`），它成为了一个极其稳定的“看门狗（Watchdog）”。

2. **实时状态感知 (Real-time State Monitoring):**
   *   两个进程之间如何对话？**Socket 就是它们的桥梁**。
   *   UE5 内部的 `pipeline.py` 像是一个打工仔，每做完一步（加载完成、生成轨迹、渲染完一帧），就通过 Socket 向外面的总管报告。
   *   总管不仅能记录进度，一旦发现 Socket 连接断开（说明引擎崩了），就能立刻触发异常处理逻辑：自动杀死卡死的进程，然后**断点续传**，重新拉起引擎接着上一帧继续渲染。这实现了真正的 7x24 小时无人值守。

3. **数据校准层（你的核心亮点）：**
   *   在图的第四阶段（黄色区域），着重强调原版插件产出的数据在导入 CV 网络（如占据网络 Occ）时，存在坐标系和深度的错配。你在 `Fixed_Track` 分支中通过严格的数学变换（旋转矩阵/深度投影修正），填补了渲染引擎和深度学习算法之间的鸿沟。