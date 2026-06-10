import streamlit as st
import os
import time

class FileExplorer:
    def __init__(self, base_directory="."):
        """Initialize the file explorer with the given base directory."""
        self.base_directory = base_directory

    def set_base_directory(self, directory):
        """Update the base directory if the given path is a valid directory."""
        if os.path.isdir(directory):
            self.base_directory = directory
        else:
            self.sb.error(f"Invalid directory: {directory}")

    def list_directory(self, directory):
        """Return (dirs, files) sorted alphabetically for the given directory path."""
        try:
            entries = os.listdir(directory)
            dirs = []
            files = []

            for entry in entries:
                full_path = os.path.join(directory, entry)
                if os.path.isdir(full_path):
                    dirs.append(entry)
                elif os.path.isfile(full_path):
                    files.append(entry)

            dirs.sort()
            files.sort()
            return dirs, files
        except Exception as e:
            self.sb.error(f"Error listing directories and files: {e}")
            return [], []

    def delete_item(self, path, is_dir):
        """Delete a file or empty directory and show a success/error message."""
        try:
            if is_dir:
                os.rmdir(path)
                self.main.success(f"Directory '{path}' successfully deleted.")
            else:
                os.remove(path)
                self.main.success(f"File '{path}' successfully deleted.")
        except Exception as e:
            self.main.error(f"Error deleting {'directory' if is_dir else 'file'} '{path}': {e}")

    def render_tree(self, directory=None, level=0):
        """Render a recursive directory tree with expand checkboxes and delete buttons."""
        if directory is None:
            directory = self.base_directory

        dirs, files = self.list_directory(directory)

        for dir_entry in dirs:
            dir_path = os.path.join(directory, dir_entry)
            is_expanded = self.main.checkbox(f"{'  ' * level}📂 {dir_entry}", key=dir_path)

            col1, _, _ = self.main.columns([8, 2, 2])
            with col1:
                if is_expanded:
                    self.render_tree(dir_path, level + 1)
#            with col2:
#                if st.button("Delete", key=f"delete_dir_{dir_path}"):

        for file_entry in files:
            file_path = os.path.join(directory, file_entry)
            file_stats = os.stat(file_path)
            file_info = f"{file_entry} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(file_stats.st_mtime))})"
            col1, col2, col3 = self.main.columns([6, 2, 2])
            with col1:
                st.write(f"{'  ' * level}📄 {file_info}")
            with col2:
                if st.button("Delete", key=f"delete_file_{file_path}"):
                    self.delete_item(file_path, is_dir=False)
            with col3:
                st.write(f"Size: {file_stats.st_size} Bytes")

    @st.fragment(run_every='60s')
    def render(self):
        """Render the full file explorer UI: directory input, sort options, upload, and tree view."""
        def save_uploaded_file(uploaded_file, path='.'):
            try:
                with open(os.path.join(path,uploaded_file.name),"wb") as f:
                    f.write(uploaded_file.getbuffer())
                return self.sb.success(f"Saved file :{uploaded_file.name} in {path}")
            except Exception:
                return self.sb.error("Error, no valid file or path")
                
        
        st.header("Advanced File Explorer")

        frame = st.empty()
        (self.sb, _, self.main) = frame.columns([1,0.1, 4])

        # Input for the base directory
        directory_input = self.sb.text_input("Enter the base directory", self.base_directory)
        self.set_base_directory(directory_input)

        # Sorting options
        sort_option = self.sb.selectbox("Sort by", ["Date", "Name"], index=0)

        if sort_option == "Date":
            self.list_directory = lambda directory: self._list_directory_sorted_by_date(directory)
        else:
            self.list_directory = lambda directory: self._list_directory_sorted_by_name(directory)

        # store file
        self.sb.html("<h4>File Uploader</h4>")        
        target_dir = self.sb.text_input("Target directory", self.base_directory)
        uploaded_file = self.sb.file_uploader("Upload an file", type=["db","xls","xlsx","txt","html"])
        if uploaded_file:
            save_uploaded_file(uploaded_file=uploaded_file, path=target_dir)

        # Render directory tree
        self.render_tree()

    def _list_directory_sorted_by_name(self, directory):
        """Return (dirs, files) both sorted alphabetically by name."""
        try:
            entries = os.listdir(directory)
            dirs = []
            files = []

            for entry in entries:
                full_path = os.path.join(directory, entry)
                if os.path.isdir(full_path):
                    dirs.append(entry)
                elif os.path.isfile(full_path):
                    files.append(entry)

            dirs.sort()
            files.sort()
            return dirs, files
        except Exception as e:
            st.error(f"Error sorting by name: {e}")
            return [], []

    def _list_directory_sorted_by_date(self, directory):
        """Return (dirs, files) both sorted by modification time, newest first."""
        try:
            entries = os.listdir(directory)
            dirs = []
            files = []

            for entry in entries:
                full_path = os.path.join(directory, entry)
                if os.path.isdir(full_path):
                    dirs.append(entry)
                elif os.path.isfile(full_path):
                    files.append(entry)

            dirs.sort(key=lambda e: os.path.getmtime(os.path.join(directory, e)), reverse=True)
            files.sort(key=lambda e: os.path.getmtime(os.path.join(directory, e)), reverse=True)
            return dirs, files
        except Exception as e:
            self.main.error(f"Error sorting by date: {e}")
            return [], []

if __name__ == "__main__":
    explorer = FileExplorer()
    explorer.render()

