import 'dart:async';
import 'package:flutter/material.dart';
import 'api.dart';
import 'player.dart';

/// Catalog screens keep metadata-only results distinct from playable audio.
class CatalogPage extends StatefulWidget {
  final LainApi api;
  final LainPlayer player;
  const CatalogPage({super.key,required this.api,required this.player});
  @override State<CatalogPage> createState()=>_CatalogPageState();
}

class _CatalogPageState extends State<CatalogPage> {
  final query=TextEditingController();
  String category='tracks';
  String difficulty='medium';
  List<dynamic> results=[];
  String description='';
  String? error;
  bool busy=false,variants=false;
  int generation=0,offset=0;
  bool more=false;
  String? pagedPath;
  bool releaseGroups=false;
  int recommendationPage=0;
  LainApi get api=>widget.api;
  @override void dispose(){generation++;query.dispose();super.dispose();}
  Future<void> load(String path,{Map<String,String>? params,bool append=false,bool paged=false})async{
    final g=++generation;
    setState((){busy=true;error=null;if(!append){results=[];description='';more=false;offset=0;}});
    try{
      final data=await api.request('GET',path,query:params) as Map;
      if(!mounted||g!=generation)return;
      setState((){
        results=[if(append)...results,...data['results'] as List];
        final seeds=data['seed_artists'];
        description=seeds is List?'На основе: ${seeds.join(', ')}':'';
        releaseGroups=path=='/v1/albums/for-you';
        recommendationPage=(data['page'] as int?)??0;
        more=(paged||releaseGroups)&&data['has_more']==true;
        pagedPath=paged||releaseGroups?path:null;
        offset=append?offset+20:20;
      });
    }catch(e){if(mounted&&g==generation)setState(()=>error=e.toString());}
    finally{if(mounted&&g==generation)setState(()=>busy=false);}
  }
  String get endpoint=>switch(category){'albums'=>'/v1/albums','artists'=>'/v1/artists',_=>'/v1/discovery'};
  Future<void> search() async {
    final q=query.text.trim();
    if(q.length<2)return;
    await load('$endpoint/search',params:{'q':q,if(category=='tracks')'variants':'$variants'});
  }
  Future<void> saved() => load(category=='tracks'?'/v1/discovery/wishlist':endpoint,paged:category!='tracks');
  void snack(String text){if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(text)));}
  Future<void> openRecording(Map row)async{
    await Navigator.of(context).push(MaterialPageRoute<void>(builder:(_)=>RecordingPage(api:api,player:widget.player,row:Map<String,dynamic>.from(row))));
  }
  Future<void> openAlbum(String id)async{
    try{
      if(releaseGroups){
        final data=await api.request('GET','/v1/release-groups/$id') as Map;
        id=data['release_id'] as String;
      }
      if(!mounted)return;
      await Navigator.of(context).push(MaterialPageRoute<void>(builder:(_)=>AlbumPage(api:api,player:widget.player,id:id)));
    }catch(e){snack(e.toString());}
  }
  Future<void> openArtist(String id)async{
    await Navigator.of(context).push(MaterialPageRoute<void>(builder:(_)=>ArtistPage(api:api,player:widget.player,id:id)));
  }
  @override Widget build(BuildContext context)=>ListView(padding:const EdgeInsets.all(12),children:[
    SegmentedButton<String>(segments:const [
      ButtonSegment(value:'tracks',label:Text('Треки')),
      ButtonSegment(value:'albums',label:Text('Альбомы')),
      ButtonSegment(value:'artists',label:Text('Артисты')),
    ],selected:{category},onSelectionChanged:(v){generation++;setState((){category=v.single;results=[];description='';error=null;busy=false;more=false;});}),
    const SizedBox(height:12),
    TextField(controller:query,onSubmitted:(_)=>search(),decoration:InputDecoration(
      hintText:'Поиск в музыкальном каталоге',suffixIcon:IconButton(onPressed:busy?null:search,icon:const Icon(Icons.search)))),
    if(category=='tracks')SwitchListTile(contentPadding:EdgeInsets.zero,title:const Text('Ремиксы и другие версии'),value:variants,onChanged:(v)=>setState(()=>variants=v)),
    Wrap(spacing:8,children:[
      OutlinedButton(onPressed:busy?null:saved,child:const Text('Сохранённые')),
      if(category=='tracks'||category=='albums')FilledButton.icon(
        onPressed:busy?null:()=>load('$endpoint/for-you',params:category=='tracks'?{'difficulty':difficulty}:null),
        icon:const Icon(Icons.auto_awesome),label:const Text('Для тебя')),
    ]),
    if(category=='tracks')DropdownButton<String>(value:difficulty,isExpanded:true,items:const [
      DropdownMenuItem(value:'easy',child:Text('Близкие к вкусу')),
      DropdownMenuItem(value:'medium',child:Text('Баланс')),
      DropdownMenuItem(value:'hard',child:Text('Смелые открытия')),
    ],onChanged:(v)=>setState(()=>difficulty=v!)),
    if(busy)const LinearProgressIndicator(),
    if(error!=null)Text(error!,style:TextStyle(color:Theme.of(context).colorScheme.error)),
    if(description.isNotEmpty)Text(description),
    if(!busy&&results.isEmpty)const Padding(padding:EdgeInsets.all(16),child:Text('Найди музыку или открой персональную подборку.')),
    for(final raw in results)
      ListTile(leading:Icon(category=='tracks'?(raw['playable']==true?Icons.play_circle:Icons.music_note):category=='albums'?Icons.album:Icons.person),
        title:Text((raw['title']??raw['name']??'') as String),
        subtitle:Text(category=='artists'?(raw['disambiguation']??'') as String:
          '${raw['artist']??''}${category=='tracks'?(raw['playable']==true?' · готово':raw['preparable']==true?' · можно подготовить':' · только каталог'):''}'),
        onTap:()=>switch(category){'tracks'=>openRecording(Map<String,dynamic>.from(raw as Map)),
          'albums'=>openAlbum(raw['mbid'] as String),'artists'=>openArtist(raw['mbid'] as String),_=>Future<void>.value()}),
    if(more&&pagedPath!=null)TextButton(onPressed:busy?null:()=>load(pagedPath!,params:releaseGroups?{'page':'${recommendationPage+1}'}:{'offset':'$offset'},append:true,paged:!releaseGroups),child:const Text('Ещё')),
  ]);
}

class RecordingPage extends StatefulWidget{
  final LainApi api;
  final LainPlayer player;
  final Map<String,dynamic> row;
  const RecordingPage({super.key,required this.api,required this.player,required this.row});
  @override State<RecordingPage> createState()=>_RecordingPageState();
}

class _RecordingPageState extends State<RecordingPage>{
  late Map<String,dynamic> row;
  bool busy=false;
  String? status;
  LainApi get api=>widget.api;
  String get id=>row['mbid'] as String;
  @override void initState(){super.initState();row=Map.of(widget.row);}
  Future<void> action(Future<void> Function() work)async{
    if(busy)return;
    setState(()=>busy=true);
    try{await work();}catch(e){if(mounted)setState(()=>status=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> react(String value)=>action(()async{
    await api.request('PUT','/v1/discovery/$id/reaction',body:{'status':value});
    if(mounted)setState((){row['decision']=value;status='Реакция сохранена';});
  });
  Future<void> save()=>action(()async{
    final value=row['in_wishlist']!=true;
    await api.request('PUT','/v1/discovery/$id/saved',body:{'saved':value});
    if(mounted)setState(()=>row['in_wishlist']=value);
  });
  Track get track=>Track(row['local_track_id'] as int,row['title'] as String,row['artist'] as String,true,false,null,row['decision'] as String?);
  Future<void> prepare()=>action(()async{
    final tid=row['local_track_id'] as int?;
    if(tid==null)return;
    final result=await api.prefetch([tid]);
    final state=result.first['state'];
    if(mounted)setState((){row['playable']=state=='ready';status=state=='ready'?'Аудио готово':state=='queued'||state=='running'?'Сервер готовит аудио. Проверь статус позже.':'Источник недоступен: $state';});
  });
  Future<void> match()async{
    final selected=await Navigator.push<Track>(context,MaterialPageRoute(builder:(_)=>MatchPage(api:api)));
    if(selected==null||!mounted)return;
    final confirm=await showDialog<bool>(context:context,builder:(ctx)=>AlertDialog(
      title:const Text('Подтвердить точное совпадение?'),
      content:Text('${row['artist']} — ${row['title']}\n\n${selected.artist} — ${selected.title}\n\nТолько если это та же запись, не другой ремикс или концертная версия.'),
      actions:[TextButton(onPressed:()=>Navigator.pop(ctx,false),child:const Text('Отмена')),
        FilledButton(onPressed:()=>Navigator.pop(ctx,true),child:const Text('Подтверждаю'))]));
    if(confirm!=true||!mounted)return;
    await action(()async{
      await api.request('PUT','/v1/discovery/$id/match',body:{'track_id':selected.id});
      if(mounted)setState((){row['local_track_id']=selected.id;row['playable']=selected.playable;status='Связь подтверждена';});
    });
  }
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Запись')),body:ListView(padding:const EdgeInsets.all(20),children:[
    const Icon(Icons.album,size:110),Text(row['title'] as String,style:Theme.of(context).textTheme.headlineMedium),Text(row['artist'] as String),
    const SizedBox(height:12),
    Text(row['playable']==true?'Аудио доступно':row['preparable']==true?'Есть одобренный источник':'Метаданные каталога · аудиофайл не получен'),
    if(row['playable']==true)FilledButton.icon(onPressed:()=>widget.player.start([track]),icon:const Icon(Icons.play_arrow),label:const Text('Слушать')),
    if(row['local_track_id']!=null&&row['playable']!=true)OutlinedButton(onPressed:busy?null:prepare,child:const Text('Подготовить / проверить')),
    OutlinedButton(onPressed:busy?null:save,child:Text(row['in_wishlist']==true?'Убрать из сохранённых':'Сохранить на потом')),
    if(row['local_track_id']==null)OutlinedButton(onPressed:busy?null:match,child:const Text('Связать с записью в медиатеке')),
    const Divider(),
    for(final e in const {'add':'Добавлю','maybe':'Может добавлю','never':'Никогда','later':'Не сейчас','interesting':'Интересно, но не слушать'}.entries)
      ListTile(title:Text(e.value),selected:row['decision']==e.key,trailing:row['decision']==e.key?const Icon(Icons.check):null,onTap:busy?null:()=>react(e.key)),
    if(busy)const LinearProgressIndicator(),
    if(status!=null)Text(status!),
    const SizedBox(height:12),SelectableText('MusicBrainz: $id'),
  ]));
}

class MatchPage extends StatefulWidget{
  final LainApi api;
  const MatchPage({super.key,required this.api});
  @override State<MatchPage> createState()=>_MatchPageState();
}
class _MatchPageState extends State<MatchPage>{
  final query=TextEditingController();
  List<Track> rows=[];
  bool busy=false;
  String? error;
  @override void dispose(){query.dispose();super.dispose();}
  Future<void> search()async{
    if(busy)return;
    setState(()=>busy=true);
    try{
      final data=await widget.api.library(query:query.text.trim());
      if(mounted)setState(()=>rows=widget.api.rows(data['results']));
    }catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Выбрать точное аудио')),body:ListView(children:[
    TextField(controller:query,onSubmitted:(_)=>search(),decoration:InputDecoration(hintText:'Название или исполнитель',suffixIcon:IconButton(onPressed:search,icon:const Icon(Icons.search)))),
    if(busy)const LinearProgressIndicator(),
    if(error!=null)Text(error!),
    for(final t in rows)ListTile(title:Text(t.title),subtitle:Text(t.artist),onTap:()=>Navigator.pop(context,t)),
  ]));
}

class AlbumPage extends StatefulWidget{
  final LainApi api;
  final LainPlayer player;
  final String id;
  const AlbumPage({super.key,required this.api,required this.player,required this.id});
  @override State<AlbumPage> createState()=>_AlbumPageState();
}
class _AlbumPageState extends State<AlbumPage>{
  Map<String,dynamic>? data;
  bool busy=false;
  String? error;
  @override void initState(){super.initState();unawaited(load());}
  Future<void> load()async{
    if(busy)return;
    setState((){busy=true;error=null;});
    try{
      final result=Map<String,dynamic>.from(await widget.api.request('GET','/v1/albums/${widget.id}') as Map);
      if(mounted)setState(()=>data=result);
    }catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> save()async{
    if(busy||data==null)return;
    final value=data!['saved']!=true;
    setState(()=>busy=true);
    try{await widget.api.request('PUT','/v1/albums/${widget.id}/saved',body:{'saved':value});
      if(mounted)setState(()=>data!['saved']=value);
    }catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  List<dynamic> get rows=>(data?['tracks']??[]) as List;
  List<Track> get ready=>rows.where((r)=>r['playable']==true&&r['local_track_id']!=null)
    .map((r)=>Track(r['local_track_id'] as int,r['title'] as String,r['artist'] as String,true,false,null,null)).toList();
  Future<void> prepare()async{
    if(busy)return;
    final ids=rows.where((r)=>r['playable']!=true&&r['preparable']==true&&r['local_track_id']!=null).map((r)=>r['local_track_id'] as int).toSet().take(5).toList();
    if(ids.isEmpty)return;
    setState(()=>busy=true);
    try{await widget.api.prefetch(ids);if(mounted)setState(()=>error='До пяти записей отправлены на подготовку. Обнови альбом позже.');}
    catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  @override Widget build(BuildContext context)=>Scaffold(appBar:AppBar(title:const Text('Альбом'),actions:[IconButton(onPressed:busy?null:load,icon:const Icon(Icons.refresh))]),body:ListView(padding:const EdgeInsets.all(16),children:[
    if(busy)const LinearProgressIndicator(),if(error!=null)Text(error!),
    if(data!=null)...[
      const Icon(Icons.album,size:110),Text(data!['title'] as String,style:Theme.of(context).textTheme.headlineMedium),
      Text(data!['artist'] as String),Text('Готово: ${ready.length} из ${rows.length}'),
      FilledButton.icon(onPressed:ready.isEmpty?null:()=>widget.player.start(ready),icon:const Icon(Icons.play_arrow),label:Text(ready.length==rows.length?'Слушать альбом':'Слушать доступные')),
      OutlinedButton(onPressed:busy?null:save,child:Text(data!['saved']==true?'Убрать из коллекции':'Сохранить альбом')),
      if(rows.any((r)=>r['preparable']==true&&r['playable']!=true))OutlinedButton(onPressed:busy?null:prepare,child:const Text('Подготовить следующие 5')),
      for(var i=0;i<rows.length;i++)ListTile(leading:Text('${i+1}'),title:Text(rows[i]['title'] as String),
        subtitle:Text(rows[i]['artist'] as String),trailing:Icon(rows[i]['playable']==true?Icons.play_circle:Icons.cloud_off),
        onTap:rows[i]['playable']==true?()=>widget.player.start(ready,initial:rows.take(i).where((r)=>r['playable']==true).length):null),
    ],
  ]));
}

class ArtistPage extends StatefulWidget{
  final LainApi api;
  final LainPlayer player;
  final String id;
  const ArtistPage({super.key,required this.api,required this.player,required this.id});
  @override State<ArtistPage> createState()=>_ArtistPageState();
}
class _ArtistPageState extends State<ArtistPage>{
  Map<String,dynamic>? data;
  List<dynamic> albums=[];
  bool busy=false,more=false,moreTracks=false;
  int page=0,trackPage=0;
  String? error;
  @override void initState(){super.initState();unawaited(load());}
  Future<void> load()async{
    if(busy)return;
    setState(()=>busy=true);
    try{final result=Map<String,dynamic>.from(await widget.api.request('GET','/v1/artists/${widget.id}') as Map);
      if(mounted)setState((){data=result;trackPage=0;moreTracks=result['has_more']==true;});
    }catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> loadTracks()async{
    if(busy||data==null)return;
    setState(()=>busy=true);
    try{
      final result=await widget.api.request('GET','/v1/artists/${widget.id}',query:{'page':'${trackPage+1}'}) as Map;
      if(mounted)setState((){data!['results']=[...data!['results'] as List,...result['results'] as List];trackPage++;moreTracks=result['has_more']==true;});
    }catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> save()async{
    if(busy||data==null)return;
    setState(()=>busy=true);
    try{final value=data!['saved']!=true&&data!['saved']!=1;
      await widget.api.request('PUT','/v1/artists/${widget.id}/saved',body:{'saved':value});
      if(mounted)setState(()=>data!['saved']=value);
    }catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> releases({bool append=false})async{
    if(busy)return;
    setState(()=>busy=true);
    try{final p=append?page+1:0;
      final result=await widget.api.request('GET','/v1/artists/${widget.id}/discography',query:{'page':'$p'}) as Map;
      if(mounted)setState((){albums=[if(append)...albums,...result['results'] as List];page=p;more=result['has_more']==true;});
    }catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> openGroup(String id)async{
    if(busy)return;
    setState(()=>busy=true);
    try{
      final result=await widget.api.request('GET','/v1/release-groups/$id') as Map;
      if(!mounted)return;
      await Navigator.push<void>(context,MaterialPageRoute(builder:(_)=>AlbumPage(api:widget.api,player:widget.player,id:result['release_id'] as String)));
    }catch(e){if(mounted)setState(()=>error=e.toString());}
    finally{if(mounted)setState(()=>busy=false);}
  }
  @override Widget build(BuildContext context){
    final tracks=data==null?<Track>[]:widget.api.rows(data!['results']);
    final ready=tracks.where((t)=>t.playable).toList();
    return Scaffold(appBar:AppBar(title:const Text('Исполнитель')),body:ListView(padding:const EdgeInsets.all(16),children:[
      if(busy)const LinearProgressIndicator(),if(error!=null)Text(error!),
      if(data!=null)...[
        const Icon(Icons.person,size:100),Text(data!['name'] as String,style:Theme.of(context).textTheme.headlineMedium),
        Text((data!['disambiguation']??'') as String),
        OutlinedButton(onPressed:busy?null:save,child:Text(data!['saved']==true||data!['saved']==1?'Убрать из избранных':'Сохранить исполнителя')),
        FilledButton(onPressed:ready.isEmpty?null:()=>widget.player.start(ready),child:const Text('Слушать доступные треки')),
        const Text('В медиатеке'),
        for(final t in tracks)ListTile(title:Text(t.title),subtitle:Text(t.artist),onTap:t.playable?()=>widget.player.start(ready,initial:ready.indexWhere((x)=>x.id==t.id)):null),
        if(moreTracks)TextButton(onPressed:busy?null:loadTracks,child:const Text('Ещё треки')),
        OutlinedButton(onPressed:busy?null:()=>releases(),child:const Text('Дискография')),
        for(final row in albums)ListTile(leading:const Icon(Icons.album),title:Text(row['title'] as String),subtitle:Text('${row['kind']} · ${row['date']}'),onTap:()=>openGroup(row['mbid'] as String)),
        if(more)TextButton(onPressed:busy?null:()=>releases(append:true),child:const Text('Ещё релизы')),
      ],
    ]));
  }
}
