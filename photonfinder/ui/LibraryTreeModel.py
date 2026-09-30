import typing

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle

from photonfinder.core import ApplicationContext
from photonfinder.models import RootAndPath
from .BackgroundLoader import LibraryRootsLoader, FilePathsLoader
from ..models import LibraryRoot


class TreeNode:
    """Base class for all nodes in the tree."""
    dir_icon: QIcon = None

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

    def tooltip(self) -> typing.Optional[str]:
        """Return the tooltip for this node, or None for no tooltip."""
        return None

    def get_icon(self, style):
        if not TreeNode.dir_icon:
            TreeNode.dir_icon = QIcon(style.standardIcon(QStyle.SP_DirIcon))
        return TreeNode.dir_icon


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
    icon: QIcon = None

    def __init__(self, parent):
        super().__init__(parent)

    def data(self) -> str:
        return "All libraries"

    def get_icon(self, style):
        if not AllLibrariesNode.icon:
            AllLibrariesNode.icon = QIcon(style.standardIcon(QStyle.SP_DriveNetIcon))
        return AllLibrariesNode.icon


class LibraryRootNode(TreeNode):
    """Library root node."""
    icon: QIcon = None

    def __init__(self, parent, library_root: LibraryRoot):
        super().__init__(parent)
        self.library_root = library_root

    def data(self) -> str:
        return self.library_root.name

    def tooltip(self) -> typing.Optional[str]:
        description = self.library_root.description
        if description:
            return f"{self.library_root.path}\n\n{description}"
        return self.library_root.path

    def get_icon(self, style):
        if not LibraryRootNode.icon:
            LibraryRootNode.icon = QIcon(style.standardIcon(QStyle.SP_DriveHDIcon))
        return LibraryRootNode.icon


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

        # Build a nested {segment: {segment: ...}} tree from the paths
        path_tree = {}
        for path in paths:
            subtree = path_tree
            for segment in path.split('/'):
                if segment:  # Skip empty segments
                    subtree = subtree.setdefault(segment, {})

        # Merge into the existing nodes, so a refresh after a rescan only inserts/removes
        # the directories that changed and keeps the view's expansion and selection intact.
        library_index = self.createIndex(library_root_node.row(), 0, library_root_node)
        self._merge_children(library_root_node, library_index, path_tree, "")

        # Mark as loaded
        library_root_node.loaded = True
        self.loaded_library_roots.add(library_root.rowid)

    def refresh_loaded_paths(self):
        """Reload the paths of all library roots whose paths were already loaded (e.g. after a rescan)."""
        all_libraries_node = self.root_node.child(0)
        for node in all_libraries_node.children:
            if isinstance(node, LibraryRootNode) and node.library_root.rowid in self.loaded_library_roots:
                self.file_paths_loader.load_paths_for_library(node.library_root)

    @staticmethod
    def _sort_key(segment: str):
        return segment.lower(), segment

    def _merge_children(self, parent_node, parent_index, subtree: dict, parent_path: str):
        """Make parent_node's children match subtree, emitting row insert/remove signals for changes."""
        # Remove directories that no longer exist
        for row in reversed(range(len(parent_node.children))):
            if parent_node.children[row].path_segment not in subtree:
                self.beginRemoveRows(parent_index, row, row)
                del parent_node.children[row]
                self.endRemoveRows()

        existing = {child.path_segment: child for child in parent_node.children}
        for segment in sorted(subtree, key=self._sort_key):
            full_path = f"{parent_path}/{segment}" if parent_path else segment
            child = existing.get(segment)
            if child is None:
                # New directory: build its whole subtree before it becomes visible to the view
                child = PathNode(parent_node, segment, full_path)
                self._build_children(child, subtree[segment], full_path)
                key = self._sort_key(segment)
                row = next((i for i, c in enumerate(parent_node.children)
                            if self._sort_key(c.path_segment) > key), len(parent_node.children))
                self.beginInsertRows(parent_index, row, row)
                parent_node.children.insert(row, child)
                self.endInsertRows()
            else:
                child_index = self.createIndex(child.row(), 0, child)
                self._merge_children(child, child_index, subtree[segment], full_path)

    def _build_children(self, parent_node, subtree: dict, parent_path: str):
        """Recursively create child nodes for a node that is not yet part of the model."""
        for segment in sorted(subtree, key=self._sort_key):
            full_path = f"{parent_path}/{segment}" if parent_path else segment
            node = PathNode(parent_node, segment, full_path)
            parent_node.children.append(node)
            self._build_children(node, subtree[segment], full_path)

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
        elif role == Qt.ToolTipRole:
            return node.tooltip()
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
                # result.append(RootAndPath(root_id=None, path=None))
                pass
            elif isinstance(item, LibraryRootNode):
                root = item.library_root
                result.append(RootAndPath(root_id=root.rowid, root_label=root.name, path=None))
            elif isinstance(item, PathNode):
                root = item.find_library_root()
                result.append(RootAndPath(root_id=root.rowid, root_label=root.name, path=item.full_path))
        return result
