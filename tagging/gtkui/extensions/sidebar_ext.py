#
# sidebar_ext.py
#
# Copyright (C) 2014 Ratanak Lun <ratanakvlun@gmail.com>
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Linking this software with other modules is making a combined work
# based on this software. Thus, the terms and conditions of the GNU
# General Public License cover the whole combination.
#
# As a special exception, the copyright holders of this software give
# you permission to link this software with independent modules to
# produce a combined work, regardless of the license terms of these
# independent modules, and to copy and distribute the resulting work
# under terms of your choice, provided that you also meet, for each
# linked module in the combined work, the terms and conditions of the
# license of that module. An independent module is a module which is
# not derived from or based on this software. If you modify this
# software, you may extend this exception to your version of the
# software, but you are not obligated to do so. If you do not wish to
# do so, delete this exception statement from your version.
#


import cPickle
import logging

import gobject
import gtk

import deluge.component

import tagging.common.tag


from deluge.ui.client import client

from tagging.common import TaggingError
from tagging.gtkui.common.gtklib import ImageMenuItem
from tagging.gtkui.common.widgets.name_input_dialog import AddTagDialog
from tagging.gtkui.common.widgets.name_input_dialog import RenameTagDialog

from tagging.gtkui.common.widgets.tag_options_dialog import (
  TagOptionsDialog)

from tagging.gtkui.common.gtklib.dnd import TreeViewDragSourceProxy
from tagging.gtkui.common.gtklib.dnd import TreeViewDragDestProxy
from tagging.gtkui.common.gtklib.dnd import DragTarget


from tagging.common import (
  MODULE_NAME, DISPLAY_NAME,
)

from tagging.common.tag import (
  ID_NULL, ID_ALL, ID_NONE, RESERVED_IDS,
)

TAG_ID = 0
TAG_DATA = 1


log = logging.getLogger(__name__)

from tagging.gtkui import RT


class SidebarExt(object):

  # Section: Initialization

  def __init__(self, plugin):

    self._plugin = plugin
    self._filterview = deluge.component.get("FilterTreeView")

    self._state = \
      self._plugin.config["daemon"][self._plugin.daemon]["sidebar_state"]

    self._store = None
    self._tree = None
    self._menu = None

    self._dnd_src_proxy = None
    self._dnd_dest_proxy = None

    self._handlers = []

    try:
      self._store = plugin.store.copy()
      if __debug__: RT.register(self._store, __name__)

      log.debug("Setting up widgets...")
      self._create_tag_tree()

      log.debug("Installing widgets...")
      self._install_tag_tree()
      self._register_handlers()
      self._enable_dnd()

      log.debug("Loading state...")
      self._load_state()
      self._scroll_to_nearest_id(self._state["selected"])

      log.debug("Creating menu...")
      self._create_menu()

      self._plugin.register_update_func(self.update_store)
    except:
      self.unload()
      raise


  def _register_handlers(self):

    self._register_handler(self._filterview.sidebar.notebook, "switch-page",
      self._on_switch_page)


  # Section: Deinitialization

  def unload(self):

    self._plugin.deregister_update_func(self.update_store)

    self._disable_dnd()

    self._deregister_handlers()

    self._uninstall_tag_tree()
    self._destroy_tag_tree()

    self._destroy_menu()
    self._destroy_store()

    self._plugin.config.save()


  def _deregister_handlers(self):

    for widget, handle in self._handlers:
      if widget.handler_is_connected(handle):
        widget.disconnect(handle)


  def _destroy_store(self):

    if self._store:
      self._store.destroy()
      self._store = None


  # Section: Public

  def is_active_page(self):

    cur_page = self._filterview.sidebar.notebook.get_current_page()
    page = self._filterview.sidebar.notebook.page_num(self._tree.get_parent())

    return cur_page == page


  def select_tags(self, ids):

    selection = self._tree.get_selection()
    selection.handler_block_by_func(self._on_selection_changed)
    selection.unselect_all()

    if ids:
      path = self._scroll_to_nearest_id(ids)
      if path:
        self._tree.set_cursor(path)

      for id in ids:
        self._select_tag(id)

    selection.handler_unblock_by_func(self._on_selection_changed)

    if not self.is_active_page():
      self._make_active_page()
    else:
      selection.emit("changed")


  def get_selected_tags(self):

    return list(self._state["selected"])


  # Section: Public: Update

  def update_store(self, store):

    def restore_adjustment(value):

      adj = self._tree.parent.get_vadjustment()
      upper = adj.get_upper() - adj.get_page_size()

      if value > upper:
        value = upper

      adj.set_value(value)


    self._store.destroy()
    self._store = store.copy()
    if __debug__: RT.register(self._store, __name__)

    value = self._tree.parent.get_vadjustment().get_value()
    gobject.idle_add(restore_adjustment, value)

    if self._tree.window:
      self._tree.window.freeze_updates()
      gobject.idle_add(self._tree.window.thaw_updates)

    selection = self._tree.get_selection()
    selection.handler_block_by_func(self._on_selection_changed)

    self._tree.set_model(self._store.model)
    self._load_state()

    selection.handler_unblock_by_func(self._on_selection_changed)


  # Section: General

  def _register_handler(self, obj, signal, func, *args, **kwargs):

    handle = obj.connect(signal, func, *args, **kwargs)
    self._handlers.append((obj, handle))


  def _get_nearest_path(self, paths):

    def get_dist_from_visible(path):

      visible = self._tree.get_visible_rect()
      column = self._tree.get_column(0)

      rect = self._tree.get_background_area(path, column)
      if rect.y < 0:
        dist = -rect.y
      else:
        dist = rect.y + rect.height - visible.height

      return dist


    if not paths:
      return None

    nearest_path = paths[0]
    nearest_dist = get_dist_from_visible(nearest_path)

    for path in paths:
      dist = get_dist_from_visible(path)
      if dist < nearest_dist:
        nearest_path = path
        nearest_dist = dist

    return nearest_path


  def _scroll_to_nearest_id(self, ids):

    paths = [y for y in (self._store.get_model_path(x) for x in ids) if y]
    path = self._get_nearest_path(paths)
    if path:
      parent_path = path[:-1]
      if parent_path:
        self._tree.expand_to_path(parent_path)

      self._tree.scroll_to_cell(path)
      return path

    return None


  # Section: Tag Tree

  def _create_tag_tree(self):

    def render_cell_data(column, cell, model, iter):

      id, data = model[iter]

      count = data["count"]

      if self._plugin.config["common"]["filter_include_subtags"]:
        count += data["descendents"]["count"]

      tag_str = "%s (%s)" % (data["name"], count)
      cell.set_property("text", tag_str)


    def search_func(model, column, key, iter):

      id, data = model[iter]

      if data["fullname"].lower().startswith(key.lower()):
        return False

      if key.endswith("/"):
        if data["fullname"].lower() == key[:-1].lower():
          self._tree.expand_to_path(model.get_path(iter))

      return True


    tree = gtk.TreeView()
    column = gtk.TreeViewColumn(DISPLAY_NAME)
    renderer = gtk.CellRendererText()

    column.pack_start(renderer, False)
    column.set_cell_data_func(renderer, render_cell_data)
    tree.append_column(column)

    tree.set_name("%s_tree_view" % MODULE_NAME)
    tree.set_headers_visible(False)
    tree.set_enable_tree_lines(True)
    tree.set_search_equal_func(search_func)
    tree.set_model(self._store.model)
    tree.get_selection().set_mode(gtk.SELECTION_MULTIPLE)

    tree.connect("button-press-event", self._on_button_pressed)
    tree.connect("row-collapsed", self._on_row_collapsed)
    tree.connect("row-expanded", self._on_row_expanded)
    tree.get_selection().connect("changed", self._on_selection_changed)

    self._tree = tree

    if __debug__: RT.register(tree, __name__)
    if __debug__: RT.register(column, __name__)
    if __debug__: RT.register(renderer, __name__)


  def _install_tag_tree(self):

    self._filterview.sidebar.add_tab(self._tree, MODULE_NAME, DISPLAY_NAME)

    # Override style so expanders are indented
    name = self._tree.get_name()
    path = self._tree.path()

    rc_string = """
        style '%s' { GtkTreeView::indent-expanders = 1 }
        widget '%s' style '%s'
    """ % (name, path, name)

    gtk.rc_parse_string(rc_string)
    gtk.rc_reset_styles(self._tree.get_toplevel().get_settings())


  def _uninstall_tag_tree(self):

    if MODULE_NAME in self._filterview.sidebar.tabs:
      self._filterview.sidebar.remove_tab(MODULE_NAME)


  def _destroy_tag_tree(self):

    if self._tree:
      self._tree.destroy()
      self._tree = None


  # Section: Context Menu

  def _create_menu(self):

    def on_add(widget):

      try:
        dialog = AddTagDialog(self._plugin, ID_NULL)
        if __debug__: RT.register(dialog, __name__)
        dialog.show()
      except:
        log.exception("Error initializing AddTagDialog")
        pass


    def on_subtag(widget):

      try:
        id = self._menu.get_title()
        dialog = AddTagDialog(self._plugin, id)
        if __debug__: RT.register(dialog, __name__)
        dialog.show()
      except:
        log.exception("Error initializing AddTagDialog")
        pass


    def on_rename(widget):

      try:
        id = self._menu.get_title()
        dialog = RenameTagDialog(self._plugin, id)
        if __debug__: RT.register(dialog, __name__)
        dialog.show()
      except:
        log.exception("Error initializing RenameTagDialog")
        pass


    def on_remove(widget):

      id = self._menu.get_title()
      client.tagging.remove_tag(id)


    def on_option(widget):

      try:
        id = self._menu.get_title()
        dialog = TagOptionsDialog(self._plugin, id)
        if __debug__: RT.register(dialog, __name__)
        dialog.show()
      except:
        log.exception("Error initializing TagOptionsDialog")
        pass


    def on_show_menu(widget):

      self._menu.show_all()

      id = self._menu.get_title()
      if id in RESERVED_IDS:
        for i in range(1, 7):
          items[i].hide()


    menu = gtk.Menu()
    menu.connect("show", on_show_menu)

    items = tagging.gtkui.common.gtklib.menu_add_items(menu, 0, (
      ((ImageMenuItem, gtk.STOCK_ADD, _("_Add Tag")), on_add),
      ((gtk.SeparatorMenuItem,),),
      ((ImageMenuItem, gtk.STOCK_ADD, _("Add Sub_tag")), on_subtag),
      ((ImageMenuItem, gtk.STOCK_EDIT, _("Re_name Tag")), on_rename),
      ((ImageMenuItem, gtk.STOCK_REMOVE, _("_Remove Tag")), on_remove),
      ((gtk.SeparatorMenuItem,),),
      ((ImageMenuItem, gtk.STOCK_PREFERENCES, _("Tag _Options")), on_option),
    ))

    self._menu = menu

    if __debug__: RT.register(self._menu, __name__)


  def _destroy_menu(self):

    if self._menu:
      self._menu.destroy()
      self._menu = None


  # Section: Drag and Drop

  def _enable_dnd(self):

    # Source Proxy

    def load_row(widget, path, col, selection, *args):

      model = widget.get_model()
      iter_ = model.get_iter(path)
      path_str = model.get_string_from_iter(iter_)
      selection.set("TEXT", 8, path_str)

      return True


    def get_drag_icon(widget, x, y):

      return (icon_single, 0, 0)


    # Destination Proxy

    def check_dest_id(widget, path, col, pos, selection, *args):

      model = widget.get_model()
      id = model[path][TAG_ID]

      if id == ID_NONE or self._store.is_user_tag(id):
        return True


    def receive_ids(widget, path, col, pos, selection, *args):

      try:
        torrent_ids = cPickle.loads(selection.data)
      except:
        return False

      model = widget.get_model()
      id = model[path][TAG_ID]

      if id == ID_NONE or self._store.is_user_tag(id):
        log.info("Setting tag %r on %r", self._store[id]["fullname"],
          torrent_ids)
        client.tagging.set_torrent_tags(torrent_ids, id)
        return True


    def check_dest_row(widget, path, col, pos, selection, *args):

      model = widget.get_model()
      id = model[path][TAG_ID]

      try:
        src_path = selection.data
        src_id = model[src_path][TAG_ID]
      except IndexError:
        return False

      if (id == src_id or tagging.common.tag.is_ancestor(src_id, id) or
          not self._store.is_user_tag(src_id)):
        return False

      if id == ID_NONE:
        children = self._store.get_descendent_ids(ID_NULL, 1)
      elif self._store.is_user_tag(id):
        children = self._store[id]["children"]
      else:
        return False

      src_name = self._store[src_id]["name"]

      for child in children:
        if child in self._store and self._store[child]["name"] == src_name:
          return False

      return True


    def receive_row(widget, path, col, pos, selection, *args):

      if not check_dest_row(widget, path, col, pos, selection, *args):
        return False

      model = widget.get_model()
      dest_id = model[path][TAG_ID]

      src_path = selection.data
      src_id = model[src_path][TAG_ID]
      src_name = self._store[src_id]["name"]

      if dest_id != ID_NONE:
        dest_name = "%s/%s" % (self._store[dest_id]["fullname"], src_name)
      else:
        dest_id = ID_NULL
        dest_name = src_name

      log.info("Renaming tag: %r -> %r", self._store[src_id]["fullname"],
        dest_name)
      client.tagging.move_tag(src_id, dest_id, src_name)

      # Default drag source will delete row on success, so return failure
      return False


    icon_single = self._tree.render_icon(gtk.STOCK_DND, gtk.ICON_SIZE_DND)

    src_target = DragTarget(
      name="tag_row",
      scope=gtk.TARGET_SAME_APP,
      action=gtk.gdk.ACTION_MOVE,
      data_func=load_row,
    )

    self._dnd_src_proxy = TreeViewDragSourceProxy(self._tree, get_drag_icon)
    self._dnd_src_proxy.add_target(src_target)

    if __debug__: RT.register(src_target, __name__)
    if __debug__: RT.register(self._dnd_src_proxy, __name__)

    ids_target = DragTarget(
      name="torrent_ids",
      scope=gtk.TARGET_SAME_APP,
      action=gtk.gdk.ACTION_MOVE,
      pos=gtk.TREE_VIEW_DROP_INTO_OR_BEFORE,
      data_func=receive_ids,
      aux_func=check_dest_id,
    )

    row_target = DragTarget(
      name="tag_row",
      scope=gtk.TARGET_SAME_APP,
      action=gtk.gdk.ACTION_MOVE,
      pos=gtk.TREE_VIEW_DROP_INTO_OR_BEFORE,
      data_func=receive_row,
      aux_func=check_dest_row,
    )

    self._dnd_dest_proxy = TreeViewDragDestProxy(self._tree)
    self._dnd_dest_proxy.add_target(ids_target)
    self._dnd_dest_proxy.add_target(row_target)

    if __debug__: RT.register(ids_target, __name__)
    if __debug__: RT.register(row_target, __name__)
    if __debug__: RT.register(self._dnd_dest_proxy, __name__)


  def _disable_dnd(self):

    if self._dnd_src_proxy:
      self._dnd_src_proxy.unload()
      self._dnd_src_proxy = None

    if self._dnd_dest_proxy:
      self._dnd_dest_proxy.unload()
      self._dnd_dest_proxy = None


  # Section: Widget State

  def _load_state(self):

    if self._plugin.initialized:
      # Load expanded tags from last session
      for id in sorted(self._state["expanded"], reverse=True):
        iter = self._store.get_model_iter(id)
        if iter and self._store.model.iter_has_child(iter):
          path = self._store.model.get_path(iter)
          self._tree.expand_to_path(path)
        else:
          self._state["expanded"].remove(id)

      # Select tags from last session
      for id in list(self._state["selected"]):
        if not self._select_tag(id):
          self._state["selected"].remove(id)


  # Section: Widget Modifiers

  def _make_active_page(self):

    page = self._filterview.sidebar.notebook.page_num(self._tree.get_parent())
    self._filterview.sidebar.notebook.set_current_page(page)


  def _select_tag(self, id):

    if id in self._store:
      path = self._store.get_model_path(id)
      if path:
        parent_path = path[:-1]
        if parent_path:
          self._tree.expand_to_path(parent_path)

        self._tree.get_selection().select_path(path)
        return True

    return False


  # Section: Widget Handlers

  def _on_button_pressed(self, widget, event):

    x, y = event.get_coords()
    path_info = widget.get_path_at_pos(int(x), int(y))
    if not path_info:
      return

    path, column, cell_x, cell_y = path_info
    id, data = widget.get_model()[path]

    if event.button == 1 and event.type == gtk.gdk._2BUTTON_PRESS:
      if self._store.is_user_tag(id):
        try:
          dialog = TagOptionsDialog(self._plugin, id)
          if __debug__: RT.register(dialog, __name__)
          dialog.show()
        except:
          log.exception("Error initializing TagOptionsDialog")
          pass
    elif event.button == 3:
      self._menu.set_title(id)
      self._menu.popup(None, None, None, event.button, event.time)
      return True


  def _on_row_expanded(self, widget, iter, path):

    id = widget.get_model()[iter][TAG_ID]

    if id not in self._state["expanded"]:
      self._state["expanded"].append(id)


  def _on_row_collapsed(self, widget, iter, path):

    id = widget.get_model()[iter][TAG_ID]

    for item in list(self._state["expanded"]):
      if tagging.common.tag.is_ancestor(id, item):
        self._state["expanded"].remove(item)

    if id in self._state["expanded"]:
      self._state["expanded"].remove(id)


  def _on_selection_changed(self, widget):

    ids = []

    model, paths = widget.get_selected_rows()
    if paths:
      for path in paths:
        id, data = model[path]
        ids.append(id)

    self._state["selected"] = ids

    if self.is_active_page():
      ext = self._plugin.get_extension("TorrentViewExt")
      if ext and not ext.is_filter(ids):
        ext.set_filter(ids)


  # Section: Deluge Handlers

  def _on_switch_page(self, widget, page, page_num):

    child = widget.get_nth_page(page_num)

    if self._tree.is_ancestor(child):
      gobject.idle_add(self._tree.get_selection().emit, "changed")
    elif self._filterview.tag_view.is_ancestor(child):
      gobject.idle_add(self._filterview.tag_view.get_selection().emit,
        "changed")
