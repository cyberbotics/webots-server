# 仿真环境集成

这里假设本地安装了 webots 并且以 --stream 选项启动了流式服务。

在 macOS 下可以通过以下命令启动流式服务：

```
open -a Webots --args --stream
```

或指定端口：

```
open -a Webots --args --stream --port=8080
```

之后从 http://localhost:1234/index.html 访问 webots 流式服务的测试页面。
