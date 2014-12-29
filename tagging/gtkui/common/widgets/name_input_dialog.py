#
# name_input_dialog.py
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


import logging

import gtk

import deluge.component

import tagging.common
import tagging.common.tag
import tagging.gtkui.common.gtklib


from twisted.python.failure import Failure

from deluge.ui.client import client

from tagging.common import TaggingError

from tagging.gtkui.common.widgets.tag_selection_menu import (
  TagSelectionMenu)

from tagging.gtkui.common.gtklib.widget_encapsulator import (
  WidgetEncapsulator)

from tagging.gtkui import RT


from tagging.common.tag import ID_NULL, RESERVED_IDS

from tagging.common.literals import (
  TITLE_ADD_TAG, TITLE_RENAME_TAG,
  STR_ADD_TAG, STR_RENAME_TAG, STR_PARENT, STR_NONE,
  ERR_TIMED_OUT, ERR_INVALID_TYPE, ERR_INVALID_TAG, ERR_INVALID_PARENT,
  ERR_TAG_EXISTS,
)


log = logging.getLogger(__name__)


class NameInputDialog(WidgetEncapsulator):

  # Section: Constants

  GLADE_FILE = tagging.common.get_resource("wnd_name_input.glade")
  ROOT_WIDGET = "wnd_name_input"

  REQUEST_TIMEOUT = 10.0

  TYPE_ADD = "add"
  TYPE_RENAME = "rename"

  DIALOG_SPECS = {
    TYPE_ADD: (_(TITLE_ADD_TAG), gtk.STOCK_ADD, STR_ADD_TAG),
    TYPE_RENAME: (_(TITLE_RENAME_TAG), gtk.STOCK_EDIT, STR_RENAME_TAG),
  }

  DIALOG_NAME = 0
  DIALOG_ICON = 1
  DIALOG_CONTEXT = 2


  # Section: Initialization

  def __init__(self, plugin, dialog_type, tag_id):

    self._plugin = plugin
    self._type = dialog_type

    if self._type == self.TYPE_ADD:
      self._parent_id = tag_id
    elif self._type == self.TYPE_RENAME:
      if tag_id in plugin.store:
        self._parent_id = tagging.common.tag.get_parent_id(tag_id)
        self._tag_id = tag_id
        self._tag_name = plugin.store[tag_id]["name"]
        self._tag_fullname = plugin.store[tag_id]["fullname"]
      else:
        raise TaggingError(ERR_INVALID_TAG)
    else:
      raise TaggingError(ERR_INVALID_TYPE)

    self._store = None
    self._menu = None

    super(NameInputDialog, self).__init__(self.GLADE_FILE, self.ROOT_WIDGET,
      "_")

    try:
      self._store = plugin.store.copy()
      self._set_parent_tag(self._parent_id)

      # Keep window alive with cyclic reference
      self._root_widget.set_data("owner", self)

      self._setup_widgets()

      self._load_state()

      self._create_menu()

      self._refresh()

      self._plugin.register_update_func(self.update_store)
      self._plugin.register_cleanup_func(self.destroy)
    except:
      self.destroy()
      raise


  # Section: Deinitialization

  def destroy(self):

    self._plugin.deregister_update_func(self.update_store)
    self._plugin.deregister_cleanup_func(self.destroy)

    self._destroy_menu()
    self._destroy_store()

    if self.valid:
      self._root_widget.set_data("owner", None)
      super(NameInputDialog, self).destroy()


  def _destroy_store(self):

    if self._store:
      self._store.destroy()
      self._store = None


  # Section: Public

  def show(self):

    self._wnd_name_input.show()


  def update_store(self, store):

    if self._type == self.TYPE_RENAME:
      if self._tag_id not in store:
        self.destroy()
        return

    self._destroy_store()
    self._store = store.copy()

    self._destroy_menu()
    self._create_menu()

    self._select_parent_tag(self._parent_id)


  # Section: General

  def _set_parent_tag(self, parent_id):

    if parent_id in self._store:
      self._parent_id = parent_id
      self._parent_name = self._store[parent_id]["name"]
      self._parent_fullname = self._store[parent_id]["fullname"]
    else:
      self._parent_id = ID_NULL
      self._parent_name = _(STR_NONE)
      self._parent_fullname = _(STR_NONE)


  def _validate(self):

    if self._parent_id != ID_NULL and self._parent_id not in self._store:
      raise TaggingError(ERR_INVALID_PARENT)

    if self._type == self.TYPE_RENAME:
      if self._tag_id not in self._store:
        raise TaggingError(ERR_INVALID_TAG)

      if (self._tag_id == self._parent_id or
          tagging.common.tag.is_ancestor(self._tag_id,
            self._parent_id)):
        raise TaggingError(ERR_INVALID_PARENT)

    name = unicode(self._txt_name.get_text(), "utf8")
    tagging.common.tag.validate_name(name)

    for id in self._store.get_descendent_ids(self._parent_id, max_depth=1):
      if name == self._store[id]["name"]:
        raise TaggingError(ERR_TAG_EXISTS)


  def _report_error(self, error):

    log.error("%s: %s", self.DIALOG_SPECS[self._type][self.DIALOG_CONTEXT],
      error)
    self._set_error(error.tr())


  # Section: Dialog: Setup

  def _setup_widgets(self):

    self._wnd_name_input.set_transient_for(
      deluge.component.get("MainWindow").window)

    spec = self.DIALOG_SPECS[self._type]
    self._wnd_name_input.set_title(spec[self.DIALOG_NAME])
    icon = self._wnd_name_input.render_icon(spec[self.DIALOG_ICON],
      gtk.ICON_SIZE_SMALL_TOOLBAR)
    self._wnd_name_input.set_icon(icon)

    self._lbl_header.set_markup("<b>%s:</b>" % _(STR_PARENT))

    self._img_error.set_from_stock(gtk.STOCK_DIALOG_ERROR,
      gtk.ICON_SIZE_SMALL_TOOLBAR)

    if self._type == self.TYPE_RENAME:
      self._btn_revert.show()
      self._txt_name.set_text(self._tag_name)
      self._txt_name.grab_focus()

    self.connect_signals({
      "do_close" : self._do_close,
      "do_submit" : self._do_submit,
      "do_open_select_menu": self._do_open_select_menu,
      "do_toggle_fullname": self._do_toggle_fullname,
      "do_check_input": self._do_check_input,
      "do_revert": self._do_revert,
    })


  # Section: Dialog: State

  def _load_state(self):

    if self._plugin.initialized:
      pos = self._plugin.config["common"]["name_input_pos"]
      if pos:
        self._wnd_name_input.move(*pos)

      size = self._plugin.config["common"]["name_input_size"]
      if size:
        self._wnd_name_input.resize(*size)

      self._tgb_fullname.set_active(
        self._plugin.config["common"]["name_input_fullname"])


  def _save_state(self):

    if self._plugin.initialized:
      self._plugin.config["common"]["name_input_pos"] = \
        list(self._wnd_name_input.get_position())

      self._plugin.config["common"]["name_input_size"] = \
        list(self._wnd_name_input.get_size())

      self._plugin.config["common"]["name_input_fullname"] = \
        self._tgb_fullname.get_active()

      self._plugin.config.save()


  # Section: Dialog: Modifiers

  def _refresh(self):

    self._do_toggle_fullname()

    if self._parent_id == ID_NULL:
      self._lbl_selected_tag.set_tooltip_text(None)
    else:
      self._lbl_selected_tag.set_tooltip_text(self._parent_fullname)

    self._do_check_input()


  def _set_error(self, message):

    if message:
      self._img_error.set_tooltip_text(message)
      self._img_error.show()
    else:
      self._img_error.hide()


  def _select_parent_tag(self, parent_id):

      self._set_parent_tag(parent_id)
      self._refresh()
      self._txt_name.grab_focus()


  # Section: Dialog: Handlers

  def _do_close(self, *args):

    self._save_state()
    self.destroy()


  def _do_submit(self, *args):

    def on_timeout():

      if self.valid:
        self._wnd_name_input.set_sensitive(True)
        self._report_error(TaggingError(ERR_TIMED_OUT))


    def process_result(result):

      if self.valid:
        self._wnd_name_input.set_sensitive(True)

        if isinstance(result, Failure):
          error = tagging.common.extract_error(result)
          if error:
            self._report_error(error)
          else:
            self.destroy()
            return result
        else:
          self._do_close()


    self._do_check_input()
    if not self._btn_ok.get_property("sensitive"):
      return

    name = unicode(self._txt_name.get_text(), "utf8")

    if self._parent_id != ID_NULL:
      dest_name = "%s/%s" % (self._parent_fullname, name)
    else:
      dest_name = name

    if self._type == self.TYPE_ADD:
      log.info("Adding tag: %r", dest_name)
      deferred = client.tagging.add_tag(self._parent_id, name)
    elif self._type == self.TYPE_RENAME:
      log.info("Renaming tag: %r -> %r", self._tag_fullname, dest_name)
      deferred = client.tagging.move_tag(self._tag_id, self._parent_id,
        name)

    self._wnd_name_input.set_sensitive(False)

    tagging.common.deferred_timeout(deferred, self.REQUEST_TIMEOUT,
      on_timeout, process_result, process_result)


  def _do_open_select_menu(self, *args):

    if self._menu:
      self._menu.popup(None, None, None, 1, gtk.gdk.CURRENT_TIME)


  def _do_toggle_fullname(self, *args):

    if self._tgb_fullname.get_active():
      self._lbl_selected_tag.set_text(self._parent_fullname)
    else:
      self._lbl_selected_tag.set_text(self._parent_name)


  def _do_check_input(self, *args):

    try:
      self._validate()
      self._btn_ok.set_sensitive(True)
      self._set_error(None)
    except TaggingError as e:
      self._btn_ok.set_sensitive(False)
      self._set_error(e.tr())


  def _do_revert(self, *args):

    if self._type != self.TYPE_RENAME:
      return

    self._txt_name.set_text(self._tag_name)

    parent_id = tagging.common.tag.get_parent_id(self._tag_id)
    self._select_parent_tag(parent_id)


  # Section: Dialog: Menu

  def _create_menu(self):

    def on_show_menu(menu):

      parent_id = tagging.common.tag.get_parent_id(self._parent_id)
      if parent_id in self._store:
        items[0].show()
      else:
        items[0].hide()


    def on_activate(widget, parent_id):

      self._select_parent_tag(parent_id)


    def on_activate_parent(widget):

      parent_id = tagging.common.tag.get_parent_id(self._parent_id)
      self._select_parent_tag(parent_id)


    root_items = (((gtk.MenuItem, _(STR_NONE)), on_activate, ID_NULL),)

    self._menu = TagSelectionMenu(self._store.model, on_activate,
      root_items=root_items)
    if __debug__: RT.register(self._menu, __name__)

    items = tagging.gtkui.common.gtklib.menu_add_items(self._menu, 1,
      (((gtk.MenuItem, _(STR_PARENT)), on_activate_parent),))
    if __debug__: RT.register(items[0], __name__)

    self._menu.connect("show", on_show_menu)
    self._menu.show_all()

    if self._type == self.TYPE_RENAME:
      item = self._menu.get_tag_item(self._tag_id)
      if item:
        item.set_sensitive(False)


  def _destroy_menu(self):

    if self._menu:
      self._menu.destroy()
      self._menu = None


# Wrapper Classes

class AddTagDialog(NameInputDialog):

  def __init__(self, plugin, parent_id=ID_NULL):

    if parent_id in RESERVED_IDS:
      parent_id = ID_NULL

    super(AddTagDialog, self).__init__(plugin, self.TYPE_ADD, parent_id)


class RenameTagDialog(NameInputDialog):

  def __init__(self, plugin, tag_id):

    if tag_id in RESERVED_IDS:
      raise TaggingError(ERR_INVALID_TAG)

    super(RenameTagDialog, self).__init__(plugin, self.TYPE_RENAME, tag_id)
