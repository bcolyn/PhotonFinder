import typing

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle

from core import ApplicationContext
from models import RootAndPath
from .loaders import LibraryRootsLoader, FilePathsLoader
from ..models import LibraryRoot


class TreeNode:
    """Base class for all nodes in the tree."""

    def __init__(self, parent=None):
        self.parent = parent
        self.children = []
        self.loaded = False

    def child_count(self) -> int:
        """Return the number of children."""
        return len(self.children)

    def child(self, row: int) -> 'TreeNode':
        """Return the child at the given row."""
        if 0 <= row < len(self.children):
            return self.children[row]
        return None

    def row(self) -> int:
        """Return the row of this node in its parent's children."""
        if self.parent:
            return self.parent.children.index(self)
        return 0

    def data(self) -> str:
        """Return the data for the given column."""
        return ""

    def get_icon(self, style):
        return QIcon(style.standardIcon(QStyle.SP_DirIcon))


class RootNode(TreeNode):
    """Hidden root node."""

    def __init__(self):
        super().__init__()
        # Add the "All libraries" node as the only child
        self.children = [AllLibrariesNode(self)]
        self.loaded = True

    def data(self) -> str:
        return "Root"


class AllLibrariesNode(TreeNode):
    """'All libraries' node."""

    def __init__(self, parent):
        super().__init__(parent)

    def data(self) -> str:
        return "All libraries"

    def get_icon(self, style):
        return QIcon(style.standardIcon(QStyle.SP_DriveNetIcon))


class LibraryRootNode(TreeNode):
    """Library root node."""

    def __init__(self, parent, library_root: LibraryRoot):
        super().__init__(parent)
        self.library_root = library_root

    def data(self) -> str:
        return self.library_root.name

    def get_icon(self, style):
        return QIcon(style.standardIcon(QStyle.SP_DriveHDIcon))


class PathNode(TreeNode):
    """Path node representing a directory in a library root."""

    def __init__(self, parent, path_segment: str, full_path: str):
        super().__init__(parent)
        self.path_segment = path_segment
        self.full_path = full_path

    def data(self) -> str:
        return self.path_segment

    def find_library_root(self) -> LibraryRoot:
        # iteratively walk up the tree until we find a library root
        node = self.parent
        while not isinstance(node, LibraryRootNode):
            node = node.parent
        return node.library_root


class LibraryTreeModel(QAbstractItemModel):
    """
    Custom tree model for the filesystemTreeView.
    The data for the model comes from the database.
    """
    ready_for_display = Signal()

    def __init__(self, context: ApplicationContext, parent=None):
        super().__init__(parent)

        # Create the root node
        self.root_node = RootNode()

        # Create the loaders for async loading
        self.library_roots_loader = LibraryRootsLoader(context)
        self.file_paths_loader = FilePathsLoader(context)

        # Connect signals
        self.library_roots_loader.library_roots_loaded.connect(self._on_library_roots_reloaded)
        self.file_paths_loader.paths_loaded.connect(self._on_paths_loaded)

        # Map to keep track of which library root's paths have been loaded
        self.loaded_library_roots = set()

        # Map to keep track of nodes by their model index
        self.nodes_by_index = {}

    def reload_library_roots(self):
        """
        Load library roots into the model.
        If library_roots is provided, use those instead of fetching from the database.
        """
        # Start async loading
        self.library_roots_loader.reload_library_roots()

    def _on_library_roots_reloaded(self, library_roots):
        """Handle the library_roots_loaded signal."""
        # Get the "All libraries" node
        all_libraries_node = self.root_node.child(0)

        # Begin model reset
        self.beginResetModel()

        # Clear existing children
        all_libraries_node.children = []
        self.loaded_library_roots.clear()

        # Add library roots as children
        for library_root in library_roots:
            all_libraries_node.children.append(LibraryRootNode(all_libraries_node, library_root))

        # Mark as loaded
        all_libraries_node.loaded = True

        # End model reset
        self.endResetModel()
        self.ready_for_display.emit()

    def _on_paths_loaded(self, library_root: LibraryRoot, paths):
        """Handle the paths_loaded signal."""
        # Find the library root node
        all_libraries_node = self.root_node.child(0)
        library_root_node = None

        for i in range(all_libraries_node.child_count()):
            node = all_libraries_node.child(i)
            if isinstance(node, LibraryRootNode) and node.library_root.rowid == library_root.rowid:
                library_root_node = node
                break

        if not library_root_node:
            return

        # Create a model index for the library root node
        library_index = self.createIndex(library_root_node.row(), 0, library_root_node)

        # Begin inserting rows
        self.beginInsertRows(library_index, 0, len(paths) - 1)

        # Build a tree structure from the paths
        path_tree = {}

        for path in paths:
            segments = path.split('/')
            current_path = ""
            parent_path = ""

            for segment in segments:
                if segment:  # Skip empty segments
                    if current_path:
                        parent_path = current_path
                        current_path += f"/{segment}"
                    else:
                        current_path = segment

                    if current_path not in path_tree:
                        path_tree[current_path] = {
                            'segment': segment,
                            'parent': parent_path,
                            'children': []
                        }

                    if parent_path and parent_path in path_tree:
                        if current_path not in path_tree[parent_path]['children']:
                            path_tree[parent_path]['children'].append(current_path)

        # Create nodes for the top-level paths (those with no parent)
        for path, info in path_tree.items():
            if not info['parent']:
                node = PathNode(library_root_node, info['segment'], path)
                library_root_node.children.append(node)

                # Recursively add child paths
                self._add_child_paths(node, path, path_tree)

        # Mark as loaded
        library_root_node.loaded = True
        self.loaded_library_roots.add(library_root.rowid)

        # End inserting rows
        self.endInsertRows()

    def _add_child_paths(self, parent_node, parent_path, path_tree):
        """Recursively add child paths to a parent node."""
        if parent_path not in path_tree:
            return

        for child_path in path_tree[parent_path]['children']:
            info = path_tree[child_path]
            node = PathNode(parent_node, info['segment'], child_path)
            parent_node.children.append(node)

            # Recursively add child paths
            self._add_child_paths(node, child_path, path_tree)

    def index(self, row, column, parent=QModelIndex()):
        """Create a model index for the given row, column, and parent."""
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        if not parent.isValid():
            parent_node = self.root_node
        else:
            parent_node = parent.internalPointer()

        child_node = parent_node.child(row)
        if child_node:
            return self.createIndex(row, column, child_node)

        return QModelIndex()

    def hasChildren(self, /, parent=...):
        parentItem = self.getItem(parent)
        if isinstance(parentItem, LibraryRootNode) and not parentItem.loaded:
            return True
        else:
            return super().hasChildren(parent)

    def parent(self, index):
        """Return the parent of the model item with the given index."""
        if not index.isValid():
            return QModelIndex()

        child_node = index.internalPointer()
        parent_node = child_node.parent

        if parent_node == self.root_node:
            return QModelIndex()

        return self.createIndex(parent_node.row(), 0, parent_node)

    def rowCount(self, parent=QModelIndex()):
        """Return the number of rows under the given parent."""
        if parent.column() > 0:
            return 0

        if not parent.isValid():
            parent_node = self.root_node
        else:
            parent_node = parent.internalPointer()

        # If this is a library root node and it's not loaded yet, load its paths
        if isinstance(parent_node, LibraryRootNode) and not parent_node.loaded:
            parent_node.loaded = True  # Mark as loaded to prevent multiple loads
            self.file_paths_loader.load_paths_for_library(parent_node.library_root)

        return parent_node.child_count()

    def columnCount(self, parent=QModelIndex()):
        """Return the number of columns for the children of the given parent."""
        return 1

    def data(self, index, role=Qt.DisplayRole):
        """Return the data stored under the given role for the item referred to by the index."""
        if not index.isValid():
            return None

        node = index.internalPointer()

        if role == Qt.DisplayRole:
            return node.data()
        elif role == Qt.DecorationRole:
            style = QApplication.style()
            return node.get_icon(style)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        """Return the header data for the given role."""
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return "Name"

        return None

    def getItem(self, index):
        """Return the item at the given index."""
        if index.isValid():
            return index.internalPointer()
        return self.root_node

    def get_roots_and_paths(self, indexes) -> typing.List[RootAndPath]:
        result = list()
        for index in indexes:
            item = self.getItem(index)
            if isinstance(item, AllLibrariesNode):
                result.append(RootAndPath(root_id=None, path=None))
            elif isinstance(item, LibraryRootNode):
                result.append(RootAndPath(root_id=item.library_root.rowid, path=None))
            elif isinstance(item, PathNode):
                result.append(RootAndPath(root_id=item.find_library_root().rowid, path=item.full_path))
        return result
