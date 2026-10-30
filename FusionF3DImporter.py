"""FusionF3DImporter —— 把本地 .f3d 文件插入到当前设计（Fusion 360 Add-In）

【这是什么】
Fusion 360 原生 UI 只能"打开"本地 f3d 文件（File > Open），
无法把本地 f3d 直接"插入到当前设计"（原生插入只支持云端数据面板文件）。
本插件补上这个缺口：选择本地 .f3d → 导入当前设计的根零部件。

【安装方式一：开发调试（推荐）】
1. 启动 Fusion 360
2. 工具栏 UTILITIES → 脚本和附加模块 (Scripts and Add-Ins)
3. 切换到"附加模块 (Add-Ins)"选项卡 → 点击绿色 "+" 号
4. 选择本文件夹（FusionF3DImporter）→ 选中 FusionF3DImporter → 点击"运行"

【安装方式二：正式安装】
在 PowerShell 中执行 .\\install.ps1，然后重启 Fusion，
在 工具 → 脚本和附加模块 → 附加模块 中勾选启用 FusionF3DImporter。

【使用】
- 工具栏（UTILITIES 页）出现"导入本地 F3D"按钮；
  另有"本地 F3D 导入"侧边面板作为备用通道（按钮失败时自动打开）
- 选择 .f3d 文件 → 导入到当前设计根零部件 → Ctrl+Z 可撤销

【技术说明】
- 官方 API ImportManager.importToTarget 不能在命令事件中调用（Autodesk 文档明示），
  因此按钮通道失败时会自动引导到侧边面板通道（面板事件不属于命令事件，不受该限制）。
- probe/ProbeContexts 探针脚本可实测各通道在本机的实际可用性。
- 仅支持 .f3d（f3z 需走云上传路径，暂不支持）。仅导入到根零部件。

【版本】0.1.0
"""

import adsk.core
import adsk.fusion
import os
import traceback

CMD_ID = 'FusionF3DImporter_ImportCmd_v2'  # v2：换 ID 以甩开 Fusion 界面缓存里旧 ID 的幽灵提升图标
PALETTE_ID = 'FusionF3DImporter_Palette'
RESOURCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')

_app = None
_ui = None
_handlers = []          # 保持事件处理器引用，防止被垃圾回收后事件失效
_cmd_defs = []          # 保持命令定义引用，防止回收导致点击事件失效
_pending_path = None    # 按钮通道受限时暂存的文件路径，交给面板通道消费


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _pick_f3d_file():
    """弹出文件选择对话框，返回选中路径；用户取消返回 ''。"""
    file_dialog = _ui.createFileDialog()
    file_dialog.title = '选择要导入的 F3D 文件'
    file_dialog.filter = 'Fusion 360 Archive (*.f3d)'
    if file_dialog.showOpen() != adsk.core.DialogResults.DialogOK:
        return ''
    return file_dialog.filename


_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'import_log.txt')


def _log(msg):
    """把导入关键步骤写入插件目录下 import_log.txt，便于远程诊断。"""
    try:
        import datetime
        with open(_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('[{}] {}\n'.format(
                datetime.datetime.now().strftime('%m-%d %H:%M:%S'), msg))
    except:
        pass


def _log_open_docs():
    """把当前所有打开文档的名字写入日志（含活动标记），用于诊断名称不匹配问题。"""
    try:
        docs = _app.documents
        names = []
        for i in range(docs.count):
            d = docs.item(i)
            mark = '*' if d == _app.activeDocument else ''
            names.append('{}{}'.format(d.name, mark))
        _log('当前打开的文档({}): {}'.format(docs.count, ' | '.join(names) or '无'))
    except Exception as err:
        _log('枚举文档失败: {}'.format(err))


def import_f3d_into_current_design(f3d_path):
    """核心导入：把本地 f3d 插入"当前活动文档"（导入目标）。

    返回 (是否成功, 消息)。
    语义与 CAD 通用心智一致：选中的 f3d = 要插入的源；点击导入时所在的设计 = 目标。
    本机实测（2026-10）本版本 Fusion 的硬限制：
      1. ImportManager.importToTarget 所有上下文均报 InternalValidationError（官方回归 bug）
      2. Documents.open 只接受云端 DataFile，不接受本地路径
    可行路径（从 F3DFileOpener 插件源码确认）：
      ImportManager.importToNewDocument(options) 可把本地 f3d 打开成新文档，
      随后复制实体插入目标并关闭新文档，实现一键导入。
    流程：确定目标 → importToNewDocument 打开源 → 复制实体 → 关源 → 插入目标。
    全程步骤写入 import_log.txt。
    """
    _log('=== 开始导入: {} ==='.format(f3d_path))
    if not f3d_path:
        return False, '未选择文件'
    if not os.path.isfile(f3d_path):
        _log('文件不存在')
        return False, '文件不存在：{}'.format(f3d_path)
    if os.path.splitext(f3d_path)[1].lower() != '.f3d':
        _log('非 f3d 文件')
        return False, '仅支持 .f3d 文件（f3z 暂不支持）'

    if not adsk.fusion.Design.cast(_app.activeProduct):
        _log('无活动设计')
        return False, '当前没有打开的设计。请先打开或新建一个设计，再执行导入。'

    # 导入目标 = 点击导入时所在的活动文档（先于任何文档切换前记录）
    target_doc = _app.activeDocument
    _log('导入目标(当前活动文档): {}'.format(target_doc.name))

    # 第 1 步：importToNewDocument 把 f3d 直接打开成新文档（本机可行路径）
    source_doc = None
    opened_by_us = False
    try:
        options = _app.importManager.createFusionArchiveImportOptions(f3d_path)
        source_doc = _app.importManager.importToNewDocument(options)
        _log('importToNewDocument: {}'.format('成功' if source_doc else '返回空'))
        if source_doc:
            opened_by_us = True
            try:
                source_doc.name = os.path.splitext(os.path.basename(f3d_path))[0]
            except Exception:
                pass
    except Exception as err:
        _log('importToNewDocument 异常: {}'.format(err))

    # 第 2 步（退路）：打开失败时，找已打开的同名文档作源
    if not source_doc:
        _log_open_docs()
        source_doc = _find_open_doc_by_basename(
            os.path.splitext(os.path.basename(f3d_path))[0])
        _log('按名称查找已打开源文档: {}'.format('找到' if source_doc else '未找到'))
        if not source_doc:
            try:
                docs = _app.documents
                names = ' | '.join(docs.item(i).name for i in range(docs.count)) or '无'
            except Exception:
                names = '未知'
            return False, ('无法打开「{}」，也未找到已打开的同名文档。\n'
                           '当前打开的文档：{}\n\n'
                           '请先用您的插件打开该模型，再切回目标设计重新点击导入。'.format(
                               os.path.basename(f3d_path), names))
    if source_doc == target_doc:
        return False, ('您当前正在查看的就是「{}」本身。\n'
                       '请先切换到要导入进的目标设计，再点击导入。'.format(target_doc.name))

    # --- 第 3 步：确保源文档活动，读取全部实体 ---
    try:
        if _app.activeDocument != source_doc:
            source_doc.activate()
            _log('已激活源文档')
        fusion_doc = adsk.fusion.FusionDocument.cast(source_doc)
        source_design = fusion_doc.design if fusion_doc else None
        if not source_design:
            _log('源文档无 design 对象')
            return False, '源文档不是有效的设计文档'
        tbm = adsk.fusion.TemporaryBRepManager.get()
        temp_bodies = []
        for body in source_design.rootComponent.bRepBodies:
            if body.isSolid:
                temp_bodies.append(tbm.copy(body))
        _log('从源文档复制实体: {} 个'.format(len(temp_bodies)))
        if opened_by_us:
            # 临时打开的源文档用完即关，不留多余标签页
            try:
                source_doc.close(False)
                _log('已关闭临时源文档')
            except Exception as close_err:
                _log('关闭临时源文档失败: {}'.format(close_err))
        if not temp_bodies:
            return False, '源文档中没有可导入的实体'
    except Exception as err:
        _log('读取实体异常: {}'.format(traceback.format_exc()))
        return False, '读取源文档失败：{}'.format(err)

    # --- 第 4 步：激活目标并插入基础实体 ---
    try:
        if _app.activeDocument != target_doc:
            target_doc.activate()
            _log('已激活目标文档')
        design = adsk.fusion.Design.cast(_app.activeProduct)
        if not design:
            _log('目标激活后无 Design')
            return False, '目标文档激活失败，请重试'
        root = design.rootComponent
        base_feature = root.features.baseFeatures.add()
        base_feature.startEdit()
        # 本版本 BRepBodies.add 只接受单个 BRepBody（不接受列表），逐个插入
        for body in temp_bodies:
            root.bRepBodies.add(body, base_feature)
        base_feature.finishEdit()
        _log('插入成功: {} 个实体'.format(len(temp_bodies)))
        return True, ('导入成功（实体方式）：{} 个实体 ← {}\n'
                      '（Ctrl+Z 可撤销；不保留零部件结构与草图）'.format(
                          len(temp_bodies), os.path.basename(f3d_path)))
    except Exception as err:
        _log('插入异常: {}'.format(traceback.format_exc()))
        return False, '插入实体失败：{}'.format(err)


def _find_open_doc_by_basename(basename):
    """按文件名查找已打开的 Fusion 文档（包含匹配，忽略*号），返回第一个匹配。"""
    key = basename.lower()
    docs = _app.documents
    for i in range(docs.count):
        doc = docs.item(i)
        if not adsk.fusion.FusionDocument.cast(doc):
            continue
        name = (doc.name or '').replace('*', '').strip()
        if key in name.lower() or name.lower() in key:
            return doc
    return None


# ---------------------------------------------------------------------------
# 面板（Palette）通道：HTMLEvent 不属于命令事件，不受 importToTarget 限制
# ---------------------------------------------------------------------------

class PaletteHTMLEventHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            html_args = adsk.core.HTMLEventArgs.cast(args)
            if html_args.action != 'import':
                return
            global _pending_path
            path = _pending_path
            _pending_path = None
            if not path:
                path = _pick_f3d_file()
                if not path:
                    return
            ok, msg = import_f3d_into_current_design(path)
            palette = _ui.palettes.itemById(PALETTE_ID)
            if palette:
                palette.sendInfoToHTML('status', msg)  # 新版 API：sendInfoToHtml 已更名 sendInfoToHTML
            if not ok:
                _ui.messageBox(msg)
        except:
            if _ui:
                _ui.messageBox('面板导入失败：\n{}'.format(traceback.format_exc()))


def _ensure_palette():
    """创建（或复用）隐藏的侧边面板——仅作为导入执行通道，不显示 UI。"""
    palettes = _ui.palettes
    palette = palettes.itemById(PALETTE_ID)
    if not palette:
        # 参数与官方 DisplayPaletteSample 一致
        palette = palettes.add(PALETTE_ID, '本地 F3D 导入', 'palette.html',
                               True, True, False, 280, 170)
        if palette:
            handler = PaletteHTMLEventHandler()
            palette.incomingFromHTML.add(handler)
            _handlers.append(handler)
            palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
    if palette:
        palette.isVisible = False  # 全程隐藏：导入结果以视口变化/失败弹窗反馈
    return palette


# ---------------------------------------------------------------------------
# 按钮（命令）通道：命令事件中禁止调用 importToTarget（官方限制，已实测确认），
# 因此点击按钮 → 选文件 → 自动转到面板通道完成导入（用户无感知）
# ---------------------------------------------------------------------------

class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            command = adsk.core.Command.cast(args.command)
            if not command:
                if _ui:
                    _ui.messageBox('args.command 转换失败（API 类型变更）')
                return
            execute_handler = CommandExecuteHandler()
            command.execute.add(execute_handler)
            _handlers.append(execute_handler)
            # 无对话框输入 → 命令创建后立即执行（官方 UI 定制示例同款行为）
        except:
            if _ui:
                _ui.messageBox('命令初始化失败：\n{}'.format(traceback.format_exc()))


class CommandExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            path = _pick_f3d_file()
            if not path:
                return  # 用户取消
            # 命令事件上下文禁止调用 importToTarget（已实测确认），
            # 转交隐藏面板自动导入：暂存文件 → 让页面回发 import，
            # 导入在 HTML 事件上下文执行，不受该限制；面板全程不可见
            global _pending_path
            _pending_path = path
            palette = _ensure_palette()
            if not palette:
                _ui.messageBox('导入通道初始化失败，请重试或重启 Fusion')
                return
            palette.sendInfoToHTML('status', '已选择 {}，正在自动导入…'.format(os.path.basename(path)))
            palette.sendInfoToHTML('autoimport', path)  # 页面延迟触发导入，等命令事件完全退出
        except:
            if _ui:
                _ui.messageBox('失败：\n{}'.format(traceback.format_exc()))


# 目标面板优先级：InsertPanel=实体页「插入」菜单（本机真实 ID，日志确认存在）；
# SolidCreatePanel=「创建」菜单（PatternMaker 同款保底，已验证外来下拉可渲染）
TARGET_PANEL_IDS = ('InsertPanel', 'SolidCreatePanel')
INSERT_DROP_ID = 'FusionF3DImporter_DropDown_v2'  # 自有下拉控件 ID（v2：同样甩开旧缓存）


def _find_insert_panel():
    """定位 SOLID 页目标面板。

    按 TARGET_PANEL_IDS 优先级取第一个存在的面板：
    InsertPanel（插入菜单，本机真实 ID）→ SolidCreatePanel（创建菜单保底）。
    实测 InsertAssemblePanel 虽同名"插入"但创建成功也不渲染，故不在候选内。
    """
    try:
        for pid in TARGET_PANEL_IDS:
            panel = _ui.allToolbarPanels.itemById(pid)
            if panel:
                return panel
    except Exception:
        pass
    return None


def _log_workspace_panels():
    """把设计工作区的面板清单写入日志（可见面板与 id 的权威映射）。"""
    try:
        ws = _ui.workspaces.itemById('FusionSolidEnvironment')
        if not ws:
            _log('设计工作区未找到')
            return
        panels = ws.toolbarPanels
        names = []
        for i in range(panels.count):
            p = panels.item(i)
            try:
                names.append('{}({})'.format(p.id, p.name))
            except Exception:
                pass
        _log('设计工作区面板({}): {}'.format(panels.count, ' | '.join(names)))
    except Exception as err:
        _log('设计工作区面板枚举失败: {}'.format(err))


def _panel_list_text():
    """列出所有面板 id/名称（诊断用）。"""
    try:
        return '\n'.join('{}: {}'.format(_ui.allToolbarPanels.item(i).id,
                                         _ui.allToolbarPanels.item(i).name)
                         for i in range(_ui.allToolbarPanels.count))
    except:
        return '面板列表获取失败：\n' + traceback.format_exc()


def _cleanup_stale_controls():
    """启动时全盘清扫历史残留控件（只保留本次将要注册的入口）。"""
    cleaned = []
    try:
        for i in range(_ui.allToolbarPanels.count):
            try:
                panel = _ui.allToolbarPanels.item(i)
                drop = panel.controls.itemById(INSERT_DROP_ID)
                if drop:
                    drop.deleteMe()
                    cleaned.append('{}:旧下拉'.format(panel.id))
                control = panel.controls.itemById(CMD_ID)
                if control:
                    control.deleteMe()
                    cleaned.append('{}:旧按钮'.format(panel.id))
            except Exception:
                pass
    except Exception:
        pass
    if cleaned:
        _log('清扫残留控件: {}'.format(' | '.join(cleaned)))


def _ensure_button():
    """创建工具栏按钮，返回状态描述列表（用于启动诊断）。"""
    status = []
    cmd_def = None
    _cleanup_stale_controls()  # 先清掉历史残留（如旧版在附加模块注册的图标）
    try:
        defs = _ui.commandDefinitions
        cmd_def = defs.itemById(CMD_ID)
        if not cmd_def:
            cmd_def = defs.addButtonDefinition(
                CMD_ID, '导入本地 F3D',
                '选择本地 .f3d 文件，导入到当前设计的根零部件。\n'
                '若按钮通道受限，将自动打开「本地 F3D 导入」备用面板。',
                RESOURCE_DIR)
            handler = CommandCreatedHandler()
            cmd_def.commandCreated.add(handler)
            _handlers.append(handler)
            _cmd_defs.append(cmd_def)  # 防止定义被回收导致点击无反应
        status.append('命令定义: OK')
    except:
        status.append('命令定义: 失败 → {}'.format(
            traceback.format_exc(limit=1).strip().replace('\n', ' | ')))
        return status
    # 位置：SOLID 页「插入」菜单（直接命令、不提升 → 仅菜单项，无工具栏图标；
    # InsertPanel 不渲染时手动把 TARGET_PANEL_IDS 调成 SolidCreatePanel 退回创建菜单）
    try:
        _log_workspace_panels()
        insert_panel = _find_insert_panel()
        _log('目标面板定位: {}'.format(
            '{}({})'.format(insert_panel.id, insert_panel.name)
            if insert_panel else '未找到'))
        if not insert_panel:
            status.append('插入菜单: 未找到目标面板（详见日志面板清单）')
        else:
            # 清理之前直接加进面板的遗留控件
            try:
                old = insert_panel.controls.itemById(CMD_ID)
                if old:
                    old.deleteMe()
                    _log('已清理遗留控件')
            except Exception:
                pass
            # 清理旧的自有下拉（防止重复运行时叠加）
            try:
                old_drop = insert_panel.controls.itemById(INSERT_DROP_ID)
                if old_drop:
                    old_drop.deleteMe()
                    _log('已清理旧下拉')
            except Exception:
                pass
            _log('addCommand 直加面板: panel={}'.format(insert_panel.id))
            # 直接把命令加入面板（无二级下拉），插入菜单里一键直达
            control = insert_panel.controls.itemById(CMD_ID)
            if not control:
                control = insert_panel.controls.addCommand(cmd_def)
                _log('addCommand 返回: {}'.format('成功' if control else 'None'))
            if control:
                try:
                    control.isVisible = True  # 新版 API 控件可能默认隐藏
                except Exception:
                    pass
                try:
                    # 不提升：避免在工具栏条上多出一个图标，只保留菜单项
                    control.isPromotedByDefault = False
                    control.isPromoted = False
                    _log('已设为仅菜单项: isPromoted={}'.format(control.isPromoted))
                except Exception:
                    _log('提升设置失败: {}'.format(traceback.format_exc(limit=1)))
                status.append('插入菜单: OK（仅菜单项，面板 id={}）'.format(
                    insert_panel.id))
            else:
                status.append('插入菜单: addCommand 返回空')
        _app.log('FusionF3DImporter: 当前所有工具栏面板：\n' + _panel_list_text())
    except:
        _log('插入菜单异常: {}'.format(traceback.format_exc()))
        status.append('插入菜单: 失败 → {}'.format(
            traceback.format_exc(limit=1).strip().replace('\n', ' | ')))
    return status


# ---------------------------------------------------------------------------
# Add-In 入口
# ---------------------------------------------------------------------------

def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface
        status = _ensure_button()
        _ensure_palette()
        _log('=== 启动诊断: {} ==='.format(' | '.join(status)))
    except:
        if _ui:
            _ui.messageBox('FusionF3DImporter 启动失败：\n{}'.format(traceback.format_exc()))


def stop(context):
    # 关闭并删除面板
    try:
        if _ui:
            palette = _ui.palettes.itemById(PALETTE_ID)
            if palette:
                palette.deleteMe()
    except:
        pass
    # 从所有工具栏面板移除自有下拉与按钮控件，并删除命令定义
    try:
        if _ui:
            for i in range(_ui.allToolbarPanels.count):
                try:
                    panel = _ui.allToolbarPanels.item(i)
                    drop = panel.controls.itemById(INSERT_DROP_ID)
                    if drop:
                        drop.deleteMe()
                    control = panel.controls.itemById(CMD_ID)
                    if control:
                        control.deleteMe()
                except:
                    pass
            cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
            if cmd_def:
                cmd_def.deleteMe()
    except:
        pass
    _handlers.clear()
