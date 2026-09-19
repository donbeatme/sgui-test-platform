"""Shared upload limits returned to the workbench UI."""
MAX_TEXT_CHARS = 100_000
MAX_CONTEXT_CHARS = 300_000
MAX_DOCUMENTS = 20
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 50
VISION_BATCH_SIZE = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 80 * 1024 * 1024


def upload_limits():
    return {
        'text_chars': MAX_TEXT_CHARS, 'context_chars': MAX_CONTEXT_CHARS,
        'documents': MAX_DOCUMENTS, 'document_bytes': MAX_DOCUMENT_BYTES,
        'images': MAX_IMAGES, 'image_bytes': MAX_IMAGE_BYTES,
        'vision_batch_size': VISION_BATCH_SIZE,
        'upload_bytes': MAX_UPLOAD_BYTES,
    }
