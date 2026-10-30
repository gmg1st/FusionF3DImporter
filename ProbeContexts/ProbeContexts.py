"""ImportManager 上下文探针 —— 实测 importToTarget / FileDialog 在不同上下文中的可用性

【用途】
官方文档称 ImportManager.importToTarget 不能在命令相关事件中调用，
但未说明具体表现。本探针在本机实测各通道行为，为插件主代码定型提供依据。

【用法】
1. 把整个 ProbeContexts 文件夹复制到：
   %appdata%\\Autodesk\\Autodesk Fusion\\API\\Scripts\\
2. Fusion 360 → 工具 (UTILITIES) → 脚本和附加模块 → 脚本 选项卡
3. 选中 ProbeContexts → 点击"运行"
4. 按弹窗指引操作（会弹两次文件选择对话框：一次测脚本上下文，一次测命令事件上下文）

【测试项】
  A. 脚本上下文：     FileDialog + importToTarget（官方 ImportManager_Sample 同上下文）
  B. 命令事件上下文： FileDialog + importToTarget（模拟插件按钮通道）
  C. 面板事件上下文： 无法从脚本触发（脚本运行结束处理器即失效），
     由主插件 FusionF3DImporter 的侧边面板实际使用时验证。

【注意】
- 导入会真实作用于当前设计（测试后 Ctrl+Z 可撤销测试导入的实体）。
- 若当前没有打开的设计，会自动新建一个设计文档用于测试。

【版本】0.1.0
"""

import adsk.core
import adsk.fusion
import traceback

CMD_ID = 'ProbeContexts_TempCmd'

_app = None
_ui = None
_results = []
_refs = []  # 保持事件处理器引用


def _pick_f3d():
    fd = _ui.createFileDialog()
    fd.title = '选择任意一个本地 .f3d 文件（用于探针测试）'
    fd.filter = 'Fusion 360 Archive (*.f3d)'
    if fd.showOpen() != adsk.core.DialogResults.DialogOK:
        return ''
    return fd.filename


def _try_import(label):
    """在当前上下文中实测 FileDialog + importToTarget，结果追加到 _results。"""
    # 附加检测：TemporaryBRepManager 与文件无关，
    # 若它也失败则说明是全局环境/会话问题，而非导入文件问题
    try:
        adsk.fusion.TemporaryBRepManager.get()
        _results.append('[{}] TemporaryBRepManager: 可用'.format(label))
    except:
        _results.append('[{}] TemporaryBRepManager: 异常 → {}'.format(
            label, traceback.format_exc(limit=1).strip().replace('\n', ' | ')))

    path = ''
    try:
        path = _pick_f3d()
        _results.append('[{}] FileDialog: 可用'.format(label))
    except:
        _results.append('[{}] FileDialog: 异常 → {}'.format(
            label, traceback.format_exc(limit=1).strip().replace('\n', ' | ')))
        return

    if not path:
        _results.append('[{}] FileDialog: 用户取消，本项测试中止'.format(label))
        return

    try:
        design = adsk.fusion.Design.cast(_app.activeProduct)
        if not design:
            _results.append('[{}] importToTarget: 未执行（没有打开的设计）'.format(label))
            return
        options = _app.importManager.createFusionArchiveImportOptions(path)
        options.isViewFit = False
        ok = _app.importManager.importToTarget(options, design.rootComponent)
        _results.append('[{}] importToTarget: {}（导入 {}，Ctrl+Z 可撤销）'.format(
            label, '成功' if ok else '返回 False', path))
    except:
        _results.append('[{}] importToTarget: 异常 → {}'.format(
            label, traceback.format_exc(limit=2).strip().replace('\n', ' | ')))


class ProbeCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            command = adsk.core.Command.cast(args.command)
            execute_handler = ProbeCommandExecuteHandler()
            command.execute.add(execute_handler)
            _refs.append(execute_handler)
        except:
            _results.append('[B 命令事件] commandCreated 异常 → {}'.format(
                traceback.format_exc(limit=1).strip().replace('\n', ' | ')))


class ProbeCommandExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        _try_import('B 命令事件')
        # 立即反馈，不依赖 execute() 是否同步
        try:
            _ui.messageBox('===== 探针中间结果 =====\n' + '\n'.join(_results))
        except:
            pass


def run(context):
    global _app, _ui
    _app = adsk.core.Application.get()
    _ui = _app.userInterface
    try:
        del _results[:]

        # 前置：确保有打开的设计（导入需要目标设计）
        if not adsk.fusion.Design.cast(_app.activeProduct):
            _app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            _results.append('[前置] 当前无设计，已自动新建一个设计文档')

        # A. 脚本上下文
        _try_import('A 脚本上下文')

        # B. 命令事件上下文：创建临时无对话框命令并以编程方式触发
        try:
            defs = _ui.commandDefinitions
            old = defs.itemById(CMD_ID)
            if old:
                old.deleteMe()
            cmd_def = defs.addButtonDefinition(CMD_ID, '探针B', 'ImportManager 上下文探针（临时命令）', './resources')
            created_handler = ProbeCommandCreatedHandler()
            cmd_def.commandCreated.add(created_handler)
            _refs.append(created_handler)
            try:
                cmd_def.execute()  # 以编程方式触发命令（无对话框命令会立即执行）
            except AttributeError:
                _results.append('[B 命令事件] CommandDefinition.execute 不可用，测试 B 跳过')
            except:
                _results.append('[B 命令事件] 触发异常 → {}'.format(
                    traceback.format_exc(limit=1).strip().replace('\n', ' | ')))
        except:
            _results.append('[B 命令事件] 命令创建失败 → {}'.format(
                traceback.format_exc(limit=2).strip().replace('\n', ' | ')))

        _ui.messageBox(
            '===== 探针结果汇总 =====\n'
            + '\n'.join(_results)
            + '\n\n说明：\n'
            + '- C 面板事件上下文无法从脚本触发，请运行主插件 FusionF3DImporter，\n'
            + '  用其侧边面板实际导入一次即可验证。\n'
            + '- 测试产生的导入可用 Ctrl+Z 撤销。')

        # 清理临时命令定义
        try:
            old = _ui.commandDefinitions.itemById(CMD_ID)
            if old:
                old.deleteMe()
        except:
            pass
    except:
        if _ui:
            _ui.messageBox('探针失败：\n{}'.format(traceback.format_exc()))
