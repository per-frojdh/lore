var editor = (function() {

	// Editor elements
	var headerField, contentField, cleanSlate, lastType, currentNodeList, savedSelection;

	// Editor Bubble elements
	var textOptions, optionsBox, boldButton, italicButton, quoteButton, urlButton, urlInput;


	function init() {

		lastRange = 0;
		composing = false;
		bindElements();

		// Set cursor position
		var range = document.createRange();
		var selection = window.getSelection();
		range.setStart(headerField, 1);
		selection.removeAllRanges();
		selection.addRange(range);

		createEventBindings();

		// Load state if storage is supported
		// if ( supportsHtmlStorage() ) {
		// 	loadState();
		// }
	}

	function createEventBindings( on ) {

		// Key up bindings
		if ( supportsHtmlStorage() ) {

			document.onkeyup = function( event ) {
				checkTextHighlighting( event );
				saveState();
			}

		} else {
			document.onkeyup = checkTextHighlighting;
		}

		// Mouse bindings
		document.onmousedown = checkTextHighlighting;
		document.onmouseup = function( event ) {

			setTimeout( function() {
				checkTextHighlighting( event );
			}, 1);
		};
		
		// Window bindings
		window.addEventListener( 'resize', function( event ) {
			updateBubblePosition();
		});

		// Scroll bindings. We limit the events, to free the ui
		// thread and prevent stuttering. See:
		// http://ejohn.org/blog/learning-from-twitter
		var scrollEnabled = true;
		document.body.addEventListener( 'scroll', function() {
			
			if ( !scrollEnabled ) {
				return;
			}
			
			scrollEnabled = true;
			
			updateBubblePosition();
			
			return setTimeout((function() {
				scrollEnabled = true;
			}), 250);
		});

		// Composition bindings. We need them to distinguish
		// IME composition from text selection
		document.addEventListener( 'compositionstart', onCompositionStart );
		document.addEventListener( 'compositionend', onCompositionEnd );

		contentField.onpaste = function(e) {
          	setTimeout(function() {
				cleanHtml()
          	}, 10);
        };
	}

	function bindElements() {

		headerField = document.querySelector( '#header' );
		contentField = document.querySelector( '#content-editor' );
		textOptions = document.querySelector( '#text-options' );
		marginOptions = document.querySelector( '#margin-options' );

		optionsBox = textOptions.querySelector( '.options' );

		boldButton = textOptions.querySelector( '.bold' );
		boldButton.onclick = onBoldClick;

		italicButton = textOptions.querySelector( '.italic' );
		italicButton.onclick = onItalicClick;

		quoteButton = textOptions.querySelector( '.quote' );
		quoteButton.onclick = onQuoteClick;

		urlButton = textOptions.querySelector( '.url' );
		urlButton.onmousedown = onUrlClick;

		urlInput = textOptions.querySelector( '.url-input' );
		urlInput.onblur = onUrlInputBlur;
		urlInput.onkeydown = onUrlInputKeyDown;
	}

	function checkTextHighlighting( event ) {

		var selection = window.getSelection();

		if ( (event.target.className === "url-input" ||
		     event.target.classList.contains( "url" ) ||
		     event.target.parentNode.classList.contains( "ui-inputs")) ) {

			currentNodeList = findNodes( selection.focusNode );
			updateBubbleStates();
			return;
		}

		// Check selections exist
		if ( selection.isCollapsed === true && lastType === false ) {

			onSelectorBlur();
		}

		// Text is selected
		if ( selection.isCollapsed === false && composing === false ) {

			currentNodeList = findNodes( selection.focusNode );

			// Find if highlighting is in the editable area
			if ( hasNode( currentNodeList, "CONTENT-EDITOR") ) {
				updateBubbleStates();
				updateBubblePosition();

				// Show the ui bubble
				textOptions.className = "bubble-bar active";
				marginOptions.className = "bubble-bar active";
			}
		}

		lastType = selection.isCollapsed;
	}
	
	function updateBubblePosition() {
		var selection = window.getSelection();
		var range = selection.getRangeAt(0);
		var boundary = range.getBoundingClientRect();
		var offset = contentField.getBoundingClientRect() // editor's position in viewport
		
		textOptions.style.top = boundary.top - offset.top - 5 /*- window.pageYOffset*/ + "px";
		textOptions.style.left = (boundary.left + boundary.right)/2 - offset.left + "px";
		
		boundary = range.startContainer.parentNode.getBoundingClientRect()
		marginOptions.style.top = boundary.top - offset.top + "px";
		marginOptions.style.left = boundary.right - offset.right + boundary.width + "px";		
	}

	function updateBubbleStates() {

		// It would be possible to use classList here, but I feel that the
		// browser support isn't quite there, and this functionality doesn't
		// warrent a shim.

		if ( hasNode( currentNodeList, 'B') ) {
			boldButton.className = "bold active"
		} else {
			boldButton.className = "bold"
		}

		if ( hasNode( currentNodeList, 'I') ) {
			italicButton.className = "italic active"
		} else {
			italicButton.className = "italic"
		}

		if ( hasNode( currentNodeList, 'BLOCKQUOTE') ) {
			quoteButton.className = "quote active"
		} else {
			quoteButton.className = "quote"
		}

		if ( hasNode( currentNodeList, 'A') ) {
			urlButton.className = "url useicons active"
		} else {
			urlButton.className = "url useicons"
		}
	}

	function onSelectorBlur() {

		textOptions.className = "bubble-bar fade";
		setTimeout( function() {

			if (textOptions.className == "bubble-bar fade") {

				textOptions.className = "bubble-bar";
				textOptions.style.top = '-999px';
				textOptions.style.left = '-999px';
			}
		}, 260 )
	}

	function findNodes( element ) {

		var nodeNames = {};

		while ( element.parentNode ) {

			if (element.id === 'content-editor') {
				nodeNames['CONTENT-EDITOR'] = true
				return nodeNames
			}
			nodeNames[element.nodeName] = true;

			element = element.parentNode;

			if ( element.nodeName === 'A' ) {
				nodeNames.url = element.href;
			}
		}

		return nodeNames;
	}

	function hasNode( nodeList, name ) {

		return !!nodeList[ name ];
	}

	function saveState( event ) {
		
		localStorage[ 'header' ] = headerField.innerHTML;
		localStorage[ 'content' ] = contentField.innerHTML;
	}

	function loadState() {

		if ( localStorage[ 'header' ] ) {
			headerField.innerHTML = localStorage[ 'header' ];
		}

		if ( localStorage[ 'content' ] ) {
			contentField.innerHTML = localStorage[ 'content' ];
		}
	}

	function onBoldClick() {
		document.execCommand( 'bold', false );
	}

	function onItalicClick() {
		document.execCommand( 'italic', false );
	}

	function onQuoteClick() {

		var nodeNames = findNodes( window.getSelection().focusNode );

		if ( hasNode( nodeNames, 'BLOCKQUOTE' ) ) {
			document.execCommand( 'formatBlock', false, 'p' );
			document.execCommand( 'outdent' );
		} else {
			document.execCommand( 'formatBlock', false, 'blockquote' );
		}
	}

	function onUrlClick() {

		if ( optionsBox.className == 'options' ) {

			optionsBox.className = 'options url-mode';

			// Set timeout here to debounce the focus action
			setTimeout( function() {

				var nodeNames = findNodes( window.getSelection().focusNode );

				if ( hasNode( nodeNames , "A" ) ) {
					urlInput.value = nodeNames.url;
				} else {
					// Symbolize text turning into a link, which is temporary, and will never be seen.
					document.execCommand( 'createLink', false, '/' );
				}

				// Since typing in the input box kills the highlighted text we need
				// to save this selection, to add the url link if it is provided.
				lastSelection = window.getSelection().getRangeAt(0);
				lastType = false;

				urlInput.focus();

			}, 100);

		} else {

			optionsBox.className = 'options';
		}
	}

	function onUrlInputKeyDown( event ) {

		if ( event.keyCode === 13 ) {
			event.preventDefault();
			applyURL( urlInput.value );
			urlInput.blur();
		}
	}

	function onUrlInputBlur( event ) {

		optionsBox.className = 'options';
		applyURL( urlInput.value );
		urlInput.value = '';

		currentNodeList = findNodes( window.getSelection().focusNode );
		updateBubbleStates();
	}

	function applyURL( url ) {

		rehighlightLastSelection();

		// Unlink any current links
		document.execCommand( 'unlink', false );

		if (url !== "") {
		
			// Insert HTTP if it doesn't exist.
			if ( !url.match("^(http|https)://") ) {

				url = "http://" + url;	
			} 

			document.execCommand( 'createLink', false, url );
		}
	}

	function rehighlightLastSelection() {

		window.getSelection().addRange( lastSelection );
	}

	function getWordCount() {
		
		var text = get_text( contentField );

		if ( text === "" ) {
			return 0
		} else {
			return text.split(/\s+/).length;
		}
	}

	function onCompositionStart ( event ) {
		composing = true;
	}

	function onCompositionEnd (event) {
		composing = false;
	}

	function cleanHtml() {
		var html = contentField.innerHTML;
		/*
		Modifications:
		div -> 	p
		b ->	strong
		h1 -> 	h2
		i ->	em

		Kept tags:
		p ->	p
		blockquote -> blockquote
		h2 -> 	h2
		h3 ->	h3
		h4 ->	h4
		ul ->	ul
		ol ->	ol
		li ->	li
		strong -> strong
		em -> 	em
		*/
		html = html.replace(/<(\w+) [^>]*>/g,'<$1>'); // remove all attr
		html = html.replace(/(<\/?)div>/gi,'$1p>'); // all divs to p		
		html = html.replace(/(<\/?)h1>/gi,'$1h2>'); // all h1 to h2
		html = html.replace(/(<\/?)strong>/gi,'$1b>'); // all b to strong
		html = html.replace(/(<\/?)em>/gi,'$1i>'); // all i to em
		html = html.replace(/(&nbsp;)+/g,' '); // make spaces
		html = html.replace(/<(\/?(p|h2|h3|h4|blockquote|ul|ol|li|i|b))>/gi,'%%%$1%%%'); // rename safe tags
		html = html.replace(/<\/?.+?>/g,''); // remove all remaining tags
		html = html.replace(/%%%(.+?)%%%/gi,'<$1>');
		// html = html.replace(/>(.+?)%%%/gi,'<$1>'); // TODO needed?
		html = html.replace(/<p>\s+<\/p>/gi,''); // remove empty p tags		
		var $t = $(contentField);
		$t.html(html);
		$t.contents().filter(function() { return this.nodeType===3;}).wrap('<p />'); // wraps plain text nodes in p
		$t.contents(':empty').remove(); // removes empty nodes
		if (!$t.html() || $t.children().length == 0) { // if no nodes, put in empty placeholder
			$t.html('<p><br></p>');
		}
		return $t.html();
	};

	function exportText(type) {
		var $t = $(contentField);
		cleanHtml()
		// $t.detach()
		$t.find('p').each(function () {
			this.innerHTML = this.innerHTML+'\n\n'
		})	
		$t.find('blockquote').each(function () {
			this.innerHTML = this.innerHTML+'\n\n'
		})	
		$t.find('i').each(function () {
			this.innerHTML = '_'+this.innerHTML+'_'
		})
		$t.find('b').each(function () {
			this.innerHTML = '**'+this.innerHTML+'**'
		})
		$t.find('h2,h3,h4').each(function () {
			var hlevel = parseInt(this.nodeName.charAt(this.nodeName.length - 1))
			this.innerHTML = Array(hlevel+1).join('#') + ' '+ this.innerHTML + '\n\n' // repeats # char right times
		})
		$t.find('ul').each(function () {
			$(this).children('li').each(function(){
				this.innerHTML = '- '+this.innerHTML + '\n'
			})
			this.innerHTML = this.innerHTML+'\n'
		})
		$t.find('ol').each(function () {
			$(this).children('li').each(function (index) {
				this.innerHTML = (index+1)+'. '+this.innerHTML + '\n'// index is the counter for li items in each ol
			})
			this.innerHTML = this.innerHTML+'\n'
		})
		var html = $t.html()
		html = html.replace(/<blockquote>/g,'<blockquote>> ') // > can't be added into innerHTML without being encoded
		html = html.replace(/<\/?.+?>/g,'') // remove all remaining tags
		return html
	}

	return {
		init: init,
		saveState: saveState,
		getWordCount: getWordCount,
		exportText: exportText
	}

})();