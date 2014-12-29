#
# torrent_view_ext.py
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

import tagging.common
import tagging.common.tag


from deluge.ui.client import client

from tagging.gtkui.common.widgets.tag_options_dialog import (
  TagOptionsDialog)

from tagging.gtkui.common.widgets.tag_selection_menu import (
  TagSelectionMenu)

from tagging.gtkui.common.gtklib.dnd import TreeViewDragSourceProxy
from tagging.gtkui.common.gtklib.dnd import DragTarget


from tagging.common import (
  DISPLAY_NAME,

  STATUS_NAME, STATUS_ID,
)

from tagging.common.tag import (
  ID_ALL, ID_NONE,
)

from tagging.common.literals import (
  TITLE_SET_FILTER, TITLE_SET_TAG, TITLE_TAG_OPTIONS,

  STR_ALL, STR_NONE, STR_PARENT, STR_SELECTED,
)


log = logging.getLogger(__name__)

from tagging.gtkui import RT


class TorrentViewExt(object):

  # Section: Initialization

  def __init__(self, plugin):

    self._plugin = plugin
    self._view = deluge.component.get("TorrentView")
    self._menubar = deluge.component.get("MenuBar")

    self._store = None

    self._menu = None
    self._sep = None
    self._submenus = []

    self._alt_menu = None

    self._dnd_src_proxy = None

    self._handlers = []

    try:
      self._store = plugin.store.copy()
      if __debug__: RT.register(self._store, __name__)

      log.debug("Installing widgets...")
      self._add_column()
      self._register_handlers()
      self._enable_dnd()

      log.debug("Creating menu...")
      self._create_menus()
      self._install_context_menu()

      self._plugin.register_update_func(self.update_store)
    except:
      self.unload()
      raise


  def _add_column(self):

    def cell_data_func(column, cell, model, row, indices):

      name = model[row][indices[0]]
      id = model[row][indices[1]]

      if self._store.is_user_tag(id):
        if self._plugin.config["common"]["torrent_view_fullname"]:
          name = self._store[id]["fullname"]
        else:
          name = self._store[id]["name"]

      cell.set_property("text", name)

    self._view.add_func_column(DISPLAY_NAME, cell_data_func,
      col_types=[str, str], status_field=[STATUS_NAME, STATUS_ID])


  def _create_menus(self):

    self._menu = self._create_context_menu()
    self._submenus = self._create_submenus()
    self._install_submenus()

    self._alt_menu = self._create_alternate_menu()


  def _install_context_menu(self):

    self._sep = self._menubar.add_torrentmenu_separator()
    self._menubar.torrentmenu.append(self._menu)

    if __debug__: RT.register(self._sep, __name__)


  def _register_handlers(self):

    self._register_handler(self._view.treeview, "button-press-event",
      self._on_view_button_press)


  # Section: Deinitialization

  def unload(self):

    self._plugin.deregister_update_func(self.update_store)

    self._disable_dnd()

    self._deregister_handlers()

    self._uninstall_view_tweaks()

    self._uninstall_context_menu()
    self._destroy_menus()

    self._destroy_store()

    self._reset_filter()
    self._remove_column()


  def _deregister_handlers(self):

    for widget, handle in self._handlers:
      widget.disconnect(handle)


  def _uninstall_view_tweaks(self):

    if hasattr(self._view, "_orig_update_view"):
      self._view.update_view = self._view._orig_update_view
      del self._view._orig_update_view


  def _uninstall_context_menu(self):

    if self._menu in self._menubar.torrentmenu:
      self._menubar.torrentmenu.remove(self._menu)

    if self._sep in self._menubar.torrentmenu:
      self._menubar.torrentmenu.remove(self._sep)

    self._sep = None


  def _destroy_menus(self):

    self._destroy_alternate_menu()

    self._submenus = []
    self._destroy_context_menu()


  def _destroy_store(self):

    if self._store:
      self._store.destroy()
      self._store = None


  def _reset_filter(self):

    if self._view.filter and self._view.filter.get(STATUS_ID) is not None:
      self._view.set_filter({})


  def _remove_column(self):

    column = self._view.columns.get(DISPLAY_NAME)
    if column:
      renderer = column.column.get_cell_renderers()[0]
      column.column.set_cell_data_func(renderer, None)

      # Workaround for Deluge removing indices in the wrong order
      column.column_indices = sorted(column.column_indices, reverse=True)

      self._view.remove_column(DISPLAY_NAME)


  # Section: Public

  def set_filter(self, ids):

    if ID_ALL in ids:
      filter = {}
    else:
      if self._plugin.config["common"]["filter_include_subtags"]:
        filter = {STATUS_ID: self._get_full_family(ids)}
      else:
        filter = {STATUS_ID: ids}

    log.debug("Setting filter: %r", filter)
    self._view.set_filter(filter)


  def is_filter(self, ids):

    if ID_ALL in ids and len(self._view.filter) == 0:
      return True

    if STATUS_ID in self._view.filter:
      tag_ids = self._view.filter[STATUS_ID]

      if self._plugin.config["common"]["filter_include_subtags"]:
        tag_ids = tagging.common.tag.get_base_ancestors(tag_ids)

      if set(ids) == set(tag_ids):
        return True

    return False


  def get_selected_torrent_tags(self):

    tag_ids = []
    torrent_ids = self._view.get_selected_torrents()

    for id in torrent_ids:
      status = self._view.get_torrent_status(id)
      tag_id = status.get(STATUS_ID) or ID_NONE
      if tag_id not in tag_ids:
        tag_ids.append(tag_id)

    return tag_ids or None


  def get_any_selected_tags(self):

    ids = self.get_selected_torrent_tags()
    if ids:
      return ids
    else:
      ext = self._plugin.get_extension("SidebarExt")
      if ext and ext.is_active_page():
        return ext.get_selected_tags()

    return None


  # Section: Public: Update

  def update_store(self, store):

    self._store.destroy()
    self._store = store.copy()
    if __debug__: RT.register(self._store, __name__)

    self._destroy_alternate_menu()
    self._alt_menu = self._create_alternate_menu()

    self._uninstall_submenus()
    self._destroy_submenus()
    self._submenus = self._create_submenus()
    self._install_submenus()


  # Section: General

  def _register_handler(self, obj, signal, func, *args, **kwargs):

    handle = obj.connect(signal, func, *args, **kwargs)
    self._handlers.append((obj, handle))


  def _get_view_column(self):

    column = self._view.columns.get(DISPLAY_NAME)
    if column:
      return column.column

    return None


  def _set_filter_sync_sidebar(self, ids):

    ext = self._plugin.get_extension("SidebarExt")
    if ext:
      ext.select_tags(ids)
    else:
      self.set_filter(ids)


  def _get_full_family(self, ids):

    tag_ids = tagging.common.tag.get_base_ancestors(ids)

    for id in list(tag_ids):
      tag_ids += self._store[id]["descendents"]["ids"]

    return tag_ids


  # Section: Context Menu

  def _create_context_menu(self):

    item = gtk.MenuItem(DISPLAY_NAME)
    item.set_submenu(gtk.Menu())

    if __debug__: RT.register(item, __name__)
    if __debug__: RT.register(item.get_submenu(), __name__)

    return item


  def _destroy_context_menu(self):

    if self._menu:
      self._menu.destroy()
      self._menu = None


  def _create_alternate_menu(self):

    item = self._create_context_menu()
    item.get_submenu().append(self._create_filter_menu())

    menu = gtk.Menu()
    menu.append(item)
    menu.show_all()

    if __debug__: RT.register(menu, __name__)

    return menu


  def _destroy_alternate_menu(self):

    if self._alt_menu:
      self._alt_menu.destroy()
      self._alt_menu = None


  # Section: Context Menu: Submenu

  def _create_submenus(self):

    menus = []
    menus.append(self._create_filter_menu())
    menus.append(self._create_set_tag_menu())
    menus.append(self._create_options_item())

    return menus


  def _destroy_submenus(self):

    while self._submenus:
      menu = self._submenus.pop()
      menu.destroy()


  def _install_submenus(self):

    submenu = self._menu.get_submenu()

    for menu in self._submenus:
      submenu.append(menu)

    self._menu.show_all()


  def _uninstall_submenus(self):

    submenu = self._menu.get_submenu()

    for menu in self._submenus:
      if menu in submenu:
        submenu.remove(menu)


  # Section: Context Menu: Submenu: Set Filter

  def _create_filter_menu(self):

    def on_activate(widget, ids):

      if not isinstance(ids, list):
        ids = [ids]

      self._set_filter_sync_sidebar(ids)


    def on_activate_parent(widget):

      ids = self.get_any_selected_tags()
      parent_id = tagging.common.tag.get_common_parent(ids)
      on_activate(widget, parent_id)


    def on_activate_selected(widget):

      ids = self.get_selected_torrent_tags()
      on_activate(widget, ids)


    def on_show_menu(widget):

      items[0].hide()
      items[1].hide()

      ids = self.get_any_selected_tags()
      parent_id = tagging.common.tag.get_common_parent(ids)
      if self._store.is_user_tag(parent_id):
        items[0].show()

      ids = self.get_selected_torrent_tags()
      if self._store.user_tags(ids):
        items[1].show()


    root_items = (
      ((gtk.MenuItem, _(STR_ALL)), on_activate, ID_ALL),
      ((gtk.MenuItem, _(STR_NONE)), on_activate, ID_NONE),
    )

    menu = TagSelectionMenu(self._store.model, on_activate,
      root_items=root_items)
    menu.connect("show", on_show_menu)

    items = tagging.gtkui.common.gtklib.menu_add_items(menu, 2,
      (
        ((gtk.MenuItem, _(STR_PARENT)), on_activate_parent),
        ((gtk.MenuItem, _(STR_SELECTED)), on_activate_selected),
      )
    )

    root = gtk.MenuItem(_(TITLE_SET_FILTER))
    root.set_submenu(menu)

    if __debug__: RT.register(menu, __name__)
    if __debug__: RT.register(root, __name__)

    return root


  # Section: Context Menu: Submenu: Set Tag

  def _create_set_tag_menu(self):

    def on_activate(widget, tag_id):

      torrent_ids = self._view.get_selected_torrents()
      if torrent_ids and tag_id in self._store:
        log.info("Setting tag %r on %r", self._store[tag_id]["fullname"],
          torrent_ids)
        client.tagging.set_torrent_tags(torrent_ids, tag_id)


    def on_activate_parent(widget):

      ids = self.get_selected_torrent_tags()
      parent_id = tagging.common.tag.get_common_parent(ids)
      on_activate(widget, parent_id)


    def on_show_menu(widget):

      items[0].hide()

      ids = self.get_selected_torrent_tags()
      parent_id = tagging.common.tag.get_common_parent(ids)
      if self._store.is_user_tag(parent_id):
        items[0].show()


    root_items = (((gtk.MenuItem, _(STR_NONE)), on_activate, ID_NONE),)

    menu = TagSelectionMenu(self._store.model, on_activate,
      root_items=root_items)
    menu.connect("show", on_show_menu)

    items = tagging.gtkui.common.gtklib.menu_add_items(menu, 1,
      (((gtk.MenuItem, _(STR_PARENT)), on_activate_parent),))

    root = gtk.MenuItem(_(TITLE_SET_TAG))
    root.set_submenu(menu)

    if __debug__: RT.register(menu, __name__)
    if __debug__: RT.register(root, __name__)

    return root


  # Section: Context Menu: Tag Options

  def _create_options_item(self):

    def on_activate(widget):

      try:
        ids = self.get_selected_torrent_tags()
        dialog = TagOptionsDialog(self._plugin, ids[0])
        if __debug__: RT.register(dialog, __name__)
        dialog.show()
      except:
        log.exception("Error initializing TagOptionsDialog")
        pass


    def on_show(widget, item):

      ids = self.get_selected_torrent_tags()
      if ids and len(ids) == 1 and self._store.is_user_tag(ids[0]):
        item.show()
      else:
        item.hide()


    item = gtk.MenuItem(_(TITLE_TAG_OPTIONS))
    item.connect("activate", on_activate)

    self._menu.get_submenu().connect("show", on_show, item)

    if __debug__: RT.register(item, __name__)

    return item


  # Section: Drag and Drop

  def _enable_dnd(self):

    def on_drag_start(widget, context):

      torrent_ids = self._view.get_selected_torrents()
      widget.set_data("dnd_data", torrent_ids)


    def load_ids(widget, path, col, selection, *args):

      torrent_ids = widget.get_data("dnd_data")
      data = cPickle.dumps(torrent_ids)
      selection.set("TEXT", 8, data)

      return True


    def get_drag_icon(widget, x, y):

      if widget.get_selection().count_selected_rows() > 1:
        pixbuf = icon_multiple
      else:
        pixbuf = icon_single

      return (pixbuf, 0, 0)


    icon_single = self._view.treeview.render_icon(gtk.STOCK_DND,
      gtk.ICON_SIZE_DND)
    icon_multiple = self._view.treeview.render_icon(gtk.STOCK_DND_MULTIPLE,
      gtk.ICON_SIZE_DND)

    src_target = DragTarget(
      name="torrent_ids",
      scope=gtk.TARGET_SAME_APP,
      action=gtk.gdk.ACTION_MOVE,
      data_func=load_ids,
    )

    self._dnd_src_proxy = TreeViewDragSourceProxy(self._view.treeview,
      get_drag_icon, on_drag_start)
    self._dnd_src_proxy.add_target(src_target)

    if __debug__: RT.register(src_target, __name__)
    if __debug__: RT.register(self._dnd_src_proxy, __name__)


  def _disable_dnd(self):

    if self._dnd_src_proxy:
      self._dnd_src_proxy.unload()
      self._dnd_src_proxy = None


  # Section: Deluge Handlers

  def _on_view_button_press(self, widget, event):

    x, y = event.get_coords()
    path_info = widget.get_path_at_pos(int(x), int(y))
    if not path_info:
      self._view.treeview.get_selection().unselect_all()
      if event.button == 3:
        self._alt_menu.popup(None, None, None, event.button, event.time)
      return

    if event.button == 1 and event.type == gtk.gdk._2BUTTON_PRESS:
      if path_info[1] == self._get_view_column():
        ids = self.get_selected_torrent_tags()
        if self.is_filter(ids):
          try:
            dialog = TagOptionsDialog(self._plugin, ids[0])
            if __debug__: RT.register(dialog, __name__)
            dialog.show()
          except:
            log.exception("Error initializing TagOptionsDialog")
            pass
        else:
          self._set_filter_sync_sidebar(ids)
